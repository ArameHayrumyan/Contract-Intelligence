"""Tenant-scoped vector storage over ChromaDB.

Every collection is named ``tenant_{tenant_id}_contracts`` and every query is
implicitly filtered to the caller's tenant. This is the single change that makes
multi-tenancy *real* rather than a UI label: today there is one tenant, but the
data layer already enforces isolation, so onboarding tenant #2 requires zero
schema changes (Architectural Constraint #2 / Section 3.5).

Embeddings use ``BAAI/bge-small-en-v1.5`` locally — no external call, no key.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from functools import lru_cache

import chromadb
from chromadb.api import ClientAPI
from chromadb.api.models.Collection import Collection
from chromadb.utils import embedding_functions
from rank_bm25 import BM25Okapi

from rag_core.config import Settings
from rag_core.schemas import Chunk

logger = logging.getLogger("rag_core.storage")

#: Tenant ids must be safe to embed in a collection name.
_TENANT_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

#: Collection "kinds" — each tenant has one collection per kind. Contracts (the
#: subject documents) and standards (the corporate reference policies) are kept
#: in separate collections so a contract is never retrieved as if it were a
#: standard, and vice versa.
KIND_CONTRACTS = "contracts"
KIND_STANDARDS = "standards"

#: Token pattern that preserves dotted/hyphenated legal identifiers as a single
#: token (e.g. "4.1.a", "section-12b") so BM25 can match exact references that a
#: naive word-split — or a semantic embedding — would shatter or smooth away.
_TOKEN_RE = re.compile(r"[a-z0-9]+(?:[.\-][a-z0-9]+)*")


def _tokenize(text: str) -> list[str]:
    """Lowercase and tokenise text for BM25, keeping legal identifiers intact.

    Args:
        text: Raw chunk or query text.

    Returns:
        The list of tokens (may be empty).
    """
    return _TOKEN_RE.findall(text.lower())


def _coerce_page(value: object) -> int | None:
    """Coerce a Chroma metadata page value to a 1-based int, or ``None``.

    Chroma metadata is a wide scalar union and uses the sentinel ``-1`` for
    "unknown" (it cannot store ``None``).

    Args:
        value: The raw ``page_number`` metadata value.

    Returns:
        The page number, or ``None`` when unknown / unparseable.
    """
    if value is None or value == -1:
        return None
    if isinstance(value, int | float | str):
        return int(value)
    return None


@dataclass(frozen=True)
class RetrievedChunk:
    """A chunk returned from a similarity query.

    Attributes:
        chunk_id: Source chunk id.
        document_id: Owning document id.
        page_number: 1-based source page, when known.
        text: Chunk text.
        distance: Vector distance (lower is more similar).
    """

    chunk_id: str
    document_id: str
    page_number: int | None
    text: str
    distance: float


@dataclass
class _Bm25Index:
    """In-memory BM25 index for one tenant, derived from persisted chunks.

    Chroma remains the single source of truth; this index is a rebuildable
    keyword-search view over the same corpus. The parallel arrays are positional:
    index ``i`` in each list describes the same chunk.

    Attributes:
        bm25: The fitted BM25 model over the tokenised corpus.
        ids: Chunk ids.
        documents: Chunk texts.
        doc_ids: Owning document id per chunk.
        pages: 1-based page number per chunk (``None`` when unknown).
    """

    bm25: BM25Okapi
    ids: list[str]
    documents: list[str]
    doc_ids: list[str]
    pages: list[int | None]


def _validate_tenant_id(tenant_id: str) -> None:
    """Guard against injection / malformed tenant ids in collection names.

    Args:
        tenant_id: The tenant identifier to validate.

    Raises:
        ValueError: If the tenant id is empty or contains unsafe characters.
    """
    if not _TENANT_ID_RE.match(tenant_id):
        raise ValueError(
            f"Invalid tenant_id {tenant_id!r}: must match {_TENANT_ID_RE.pattern}"
        )


@lru_cache(maxsize=1)
def _embedding_function(model_name: str) -> embedding_functions.EmbeddingFunction:  # type: ignore[type-arg]
    """Build (and cache) the local sentence-transformers embedding function.

    Args:
        model_name: Hugging Face model id.

    Returns:
        A Chroma-compatible embedding function.
    """
    logger.info("Loading local embedding model: %s", model_name)
    return embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name=model_name
    )


class TenantVectorStore:
    """Tenant-scoped wrapper around a persistent ChromaDB client.

    The wrapper never exposes a way to access another tenant's collection: the
    only public methods take a ``tenant_id`` and resolve the namespaced
    collection internally.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        client: ClientAPI | None = None,
        embedding_function: embedding_functions.EmbeddingFunction | None = None,  # type: ignore[type-arg]
    ) -> None:
        """Initialise the store.

        Args:
            settings: Application settings (persist dir, embedding model).
            client: Optional pre-built Chroma client (used by tests to inject an
                in-memory ``EphemeralClient``).
            embedding_function: Optional embedding function override. Defaults to
                the local sentence-transformers model; tests can inject a
                deterministic fake to avoid loading the model.
        """
        self._settings = settings
        if client is not None:
            self._client = client
        else:
            settings.chroma_persist_dir.mkdir(parents=True, exist_ok=True)
            self._client = chromadb.PersistentClient(
                path=str(settings.chroma_persist_dir)
            )
        self._embed = embedding_function or _embedding_function(
            settings.embedding_model
        )
        # Per-(tenant, kind) BM25 indexes, lazily (re)built from the corpus.
        self._bm25: dict[tuple[str, str], _Bm25Index] = {}
        self._bm25_lock = threading.Lock()

    @staticmethod
    def collection_name(tenant_id: str, kind: str = KIND_CONTRACTS) -> str:
        """Return the namespaced collection name for a tenant and kind.

        Args:
            tenant_id: The tenant identifier.
            kind: ``KIND_CONTRACTS`` or ``KIND_STANDARDS``.

        Returns:
            The collection name, e.g. ``tenant_acme_contracts`` or
            ``tenant_acme_standards``.
        """
        _validate_tenant_id(tenant_id)
        return f"tenant_{tenant_id}_{kind}"

    def _collection(self, tenant_id: str, kind: str = KIND_CONTRACTS) -> Collection:
        """Get-or-create the tenant's collection for a kind.

        Args:
            tenant_id: The tenant identifier.
            kind: ``KIND_CONTRACTS`` or ``KIND_STANDARDS``.

        Returns:
            The Chroma :class:`Collection` for the tenant and kind.
        """
        return self._client.get_or_create_collection(
            name=self.collection_name(tenant_id, kind),
            embedding_function=self._embed,
            metadata={"hnsw:space": "cosine", "tenant_id": tenant_id},
        )

    def add_chunks(self, tenant_id: str, chunks: list[Chunk]) -> None:
        """Persist chunks into the tenant's collection.

        Args:
            tenant_id: The tenant identifier (must match every chunk).
            chunks: Chunks to persist.

        Raises:
            ValueError: If any chunk's ``tenant_id`` does not match ``tenant_id``
                (defence-in-depth against cross-tenant writes), or if empty.
        """
        if not chunks:
            return
        mismatched = [c.chunk_id for c in chunks if c.tenant_id != tenant_id]
        if mismatched:
            raise ValueError(
                f"Refusing cross-tenant write; mismatched chunks: {mismatched}"
            )

        collection = self._collection(tenant_id)
        collection.add(
            ids=[c.chunk_id for c in chunks],
            documents=[c.text for c in chunks],
            metadatas=[
                {
                    "document_id": c.document_id,
                    "tenant_id": c.tenant_id,
                    # Chroma metadata cannot hold None; sentinel -1 means unknown.
                    "page_number": c.page_number if c.page_number is not None else -1,
                }
                for c in chunks
            ],
        )
        logger.info(
            "Persisted %d chunks for tenant=%s document=%s",
            len(chunks),
            tenant_id,
            chunks[0].document_id,
        )
        # BM25 needs the full corpus, not a single embedding, so rebuild the
        # tenant's keyword index now that its collection has grown.
        self._rebuild_bm25(tenant_id)

    def _rebuild_bm25(self, tenant_id: str, kind: str = KIND_CONTRACTS) -> None:
        """Rebuild a (tenant, kind) BM25 index from the persisted Chroma corpus.

        Chroma is the source of truth; this reads every chunk for the tenant and
        refits BM25 so the keyword view always reflects the full corpus (and is
        recoverable after a process restart).

        Args:
            tenant_id: The tenant whose index to rebuild.
            kind: ``KIND_CONTRACTS`` or ``KIND_STANDARDS``.
        """
        collection = self._collection(tenant_id, kind)
        data = collection.get(include=["documents", "metadatas"])
        ids = data.get("ids") or []
        if not ids:
            with self._bm25_lock:
                self._bm25.pop((tenant_id, kind), None)
            return

        documents = [d or "" for d in (data.get("documents") or [])]
        metadatas = data.get("metadatas") or []
        doc_ids: list[str] = []
        pages: list[int | None] = []
        for meta in metadatas:
            doc_ids.append(str(meta.get("document_id", "")) if meta else "")
            page = meta.get("page_number", -1) if meta else -1
            pages.append(_coerce_page(page))

        index = _Bm25Index(
            bm25=BM25Okapi([_tokenize(text) for text in documents]),
            ids=list(ids),
            documents=documents,
            doc_ids=doc_ids,
            pages=pages,
        )
        with self._bm25_lock:
            self._bm25[(tenant_id, kind)] = index
        logger.info(
            "Rebuilt BM25 index for tenant=%s kind=%s corpus=%d",
            tenant_id,
            kind,
            len(ids),
        )

    def bm25_query(
        self,
        tenant_id: str,
        query_text: str,
        *,
        top_k: int = 10,
        document_ids: list[str] | None = None,
        kind: str = KIND_CONTRACTS,
    ) -> list[RetrievedChunk]:
        """Run a BM25 keyword query within the tenant's corpus.

        Complements :meth:`query` (semantic): BM25 surfaces exact lexical matches
        — article numbers, defined terms, identifiers like ``Section 4.1.a`` —
        that vector similarity can miss. Returns the same :class:`RetrievedChunk`
        shape so results fuse with vector results via the existing RRF.

        Args:
            tenant_id: The tenant identifier.
            query_text: The query string.
            top_k: Maximum number of chunks to return.
            document_ids: Optional document subset to restrict the search to.
            kind: Which collection to search (contracts or standards).

        Returns:
            Matching chunks ordered by descending BM25 relevance.
        """
        with self._bm25_lock:
            index = self._bm25.get((tenant_id, kind))
        if index is None:
            # Lazy build (e.g. after a restart, before any new ingestion).
            self._rebuild_bm25(tenant_id, kind)
            with self._bm25_lock:
                index = self._bm25.get((tenant_id, kind))
        if index is None:
            return []

        tokens = _tokenize(query_text)
        if not tokens:
            return []

        scores = index.bm25.get_scores(tokens)
        allowed = set(document_ids) if document_ids else None
        ranked = sorted(
            (
                (position, float(score))
                for position, score in enumerate(scores)
                if score > 0
                and (allowed is None or index.doc_ids[position] in allowed)
            ),
            key=lambda pair: pair[1],
            reverse=True,
        )

        results: list[RetrievedChunk] = []
        for position, score in ranked[:top_k]:
            results.append(
                RetrievedChunk(
                    chunk_id=index.ids[position],
                    document_id=index.doc_ids[position],
                    page_number=index.pages[position],
                    text=index.documents[position],
                    # Map relevance to a pseudo-distance (lower = better) so the
                    # field stays consistent with vector hits; only rank order
                    # matters to RRF.
                    distance=1.0 / (1.0 + score),
                )
            )
        return results

    def query(
        self,
        tenant_id: str,
        query_text: str,
        *,
        top_k: int = 10,
        document_ids: list[str] | None = None,
        kind: str = KIND_CONTRACTS,
    ) -> list[RetrievedChunk]:
        """Run a similarity query within the tenant's collection.

        The tenant scoping is structural (separate collection) *and* enforced via
        metadata filtering — defence in depth.

        Args:
            tenant_id: The tenant identifier.
            query_text: The query string.
            top_k: Maximum number of chunks to return.
            document_ids: Optional document subset to restrict the search to.
            kind: Which collection to search (contracts or standards).

        Returns:
            Matching chunks ordered by ascending distance.
        """
        collection = self._collection(tenant_id, kind)
        if collection.count() == 0:
            return []

        where: dict[str, object] = {"tenant_id": tenant_id}
        if document_ids:
            where = {
                "$and": [
                    {"tenant_id": tenant_id},
                    {"document_id": {"$in": document_ids}},
                ]
            }

        result = collection.query(
            query_texts=[query_text],
            n_results=min(top_k, collection.count()),
            where=where,  # type: ignore[arg-type]
            include=["documents", "metadatas", "distances"],
        )
        return self._unpack(result)

    @staticmethod
    def _unpack(result: chromadb.QueryResult) -> list[RetrievedChunk]:
        """Flatten a single-query Chroma result into :class:`RetrievedChunk`s.

        Args:
            result: Raw Chroma query result.

        Returns:
            The retrieved chunks.
        """
        ids = (result.get("ids") or [[]])[0]
        docs = (result.get("documents") or [[]])[0]
        metas = (result.get("metadatas") or [[]])[0]
        dists = (result.get("distances") or [[]])[0]

        chunks: list[RetrievedChunk] = []
        for chunk_id, text, meta, dist in zip(ids, docs, metas, dists, strict=False):
            page = meta.get("page_number", -1) if meta else -1
            chunks.append(
                RetrievedChunk(
                    chunk_id=chunk_id,
                    document_id=str(meta.get("document_id", "")) if meta else "",
                    page_number=_coerce_page(page),
                    text=text or "",
                    distance=float(dist),
                )
            )
        return chunks

    def delete_document(self, tenant_id: str, document_id: str) -> None:
        """Remove all chunks for a document from the tenant's collection.

        Args:
            tenant_id: The tenant identifier.
            document_id: The document to purge.
        """
        collection = self._collection(tenant_id)
        collection.delete(
            where={"$and": [{"tenant_id": tenant_id}, {"document_id": document_id}]}
        )
        # Keep the keyword index consistent with the shrunk corpus.
        self._rebuild_bm25(tenant_id)
        logger.info("Deleted chunks for tenant=%s document=%s", tenant_id, document_id)

    # --- Standards (corporate reference policies) ----------------------------

    def add_standard_chunks(
        self,
        tenant_id: str,
        chunks: list[Chunk],
        *,
        standard_version: str,
        standard_name: str,
    ) -> None:
        """Persist chunks of a standard document into the standards collection.

        Each chunk's ``document_id`` is the ``standard_document_id``; the version
        and name are constant for the upload and stored on every chunk so the
        listing endpoint can group versions without a side table. Standards are
        append-only and versioned — a new version is a new ``standard_document_id``
        and never overwrites a previous one.

        Args:
            tenant_id: The owning tenant (must match every chunk).
            chunks: Chunks to persist (``document_id`` == standard document id).
            standard_version: Version label for this standard document.
            standard_name: Human-readable standard name (grouping key).

        Raises:
            ValueError: If any chunk's ``tenant_id`` does not match ``tenant_id``.
        """
        if not chunks:
            return
        mismatched = [c.chunk_id for c in chunks if c.tenant_id != tenant_id]
        if mismatched:
            raise ValueError(
                f"Refusing cross-tenant standard write; mismatched: {mismatched}"
            )

        collection = self._collection(tenant_id, KIND_STANDARDS)
        collection.add(
            ids=[c.chunk_id for c in chunks],
            documents=[c.text for c in chunks],
            metadatas=[
                {
                    "document_id": c.document_id,
                    "tenant_id": c.tenant_id,
                    "page_number": c.page_number if c.page_number is not None else -1,
                    "standard_version": standard_version,
                    "standard_name": standard_name,
                }
                for c in chunks
            ],
        )
        self._rebuild_bm25(tenant_id, KIND_STANDARDS)
        logger.info(
            "Persisted %d standard chunks tenant=%s standard=%s version=%s",
            len(chunks),
            tenant_id,
            chunks[0].document_id,
            standard_version,
        )

    def query_standards(
        self,
        tenant_id: str,
        query_text: str,
        *,
        standard_document_id: str,
        top_k: int = 10,
    ) -> list[RetrievedChunk]:
        """Vector search within a single standard document.

        Args:
            tenant_id: The tenant identifier.
            query_text: The query string.
            standard_document_id: Restrict the search to this standard version.
            top_k: Maximum number of chunks to return.

        Returns:
            Matching standard chunks ordered by ascending distance.
        """
        return self.query(
            tenant_id,
            query_text,
            top_k=top_k,
            document_ids=[standard_document_id],
            kind=KIND_STANDARDS,
        )

    def bm25_query_standards(
        self,
        tenant_id: str,
        query_text: str,
        *,
        standard_document_id: str,
        top_k: int = 10,
    ) -> list[RetrievedChunk]:
        """BM25 keyword search within a single standard document.

        Args:
            tenant_id: The tenant identifier.
            query_text: The query string.
            standard_document_id: Restrict the search to this standard version.
            top_k: Maximum number of chunks to return.

        Returns:
            Matching standard chunks ordered by descending BM25 relevance.
        """
        return self.bm25_query(
            tenant_id,
            query_text,
            top_k=top_k,
            document_ids=[standard_document_id],
            kind=KIND_STANDARDS,
        )

    def get_standard_version(self, tenant_id: str, standard_document_id: str) -> str:
        """Return the version label stored on a standard document's chunks.

        Args:
            tenant_id: The tenant identifier.
            standard_document_id: The standard document id.

        Returns:
            The version label, or ``""`` if the standard is unknown.
        """
        collection = self._collection(tenant_id, KIND_STANDARDS)
        data = collection.get(
            where={
                "$and": [
                    {"tenant_id": tenant_id},
                    {"document_id": standard_document_id},
                ]
            },
            include=["metadatas"],
            limit=1,
        )
        metas = data.get("metadatas") or []
        if metas and metas[0]:
            return str(metas[0].get("standard_version", ""))
        return ""

    def get_document_chunks(
        self,
        tenant_id: str,
        document_id: str,
        *,
        kind: str = KIND_CONTRACTS,
    ) -> list[RetrievedChunk]:
        """Return every persisted chunk of one document (for inventory phases).

        Args:
            tenant_id: The tenant identifier.
            document_id: The document (or standard) id to read.
            kind: Which collection to read from.

        Returns:
            All chunks for the document (``distance`` is 0.0 — not a ranking).
        """
        collection = self._collection(tenant_id, kind)
        data = collection.get(
            where={"$and": [{"tenant_id": tenant_id}, {"document_id": document_id}]},
            include=["documents", "metadatas"],
        )
        ids = data.get("ids") or []
        docs = data.get("documents") or []
        metas = data.get("metadatas") or []
        chunks: list[RetrievedChunk] = []
        for chunk_id, text, meta in zip(ids, docs, metas, strict=False):
            page = meta.get("page_number", -1) if meta else -1
            chunks.append(
                RetrievedChunk(
                    chunk_id=chunk_id,
                    document_id=document_id,
                    page_number=_coerce_page(page),
                    text=text or "",
                    distance=0.0,
                )
            )
        return chunks
