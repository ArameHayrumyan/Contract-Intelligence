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
from dataclasses import dataclass
from functools import lru_cache

import chromadb
from chromadb.api import ClientAPI
from chromadb.api.models.Collection import Collection
from chromadb.utils import embedding_functions

from rag_core.config import Settings
from rag_core.schemas import Chunk

logger = logging.getLogger("rag_core.storage")

#: Tenant ids must be safe to embed in a collection name.
_TENANT_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


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

    def __init__(self, settings: Settings, *, client: ClientAPI | None = None) -> None:
        """Initialise the store.

        Args:
            settings: Application settings (persist dir, embedding model).
            client: Optional pre-built Chroma client (used by tests to inject an
                in-memory ``EphemeralClient``).
        """
        self._settings = settings
        if client is not None:
            self._client = client
        else:
            settings.chroma_persist_dir.mkdir(parents=True, exist_ok=True)
            self._client = chromadb.PersistentClient(
                path=str(settings.chroma_persist_dir)
            )
        self._embed = _embedding_function(settings.embedding_model)

    @staticmethod
    def collection_name(tenant_id: str) -> str:
        """Return the namespaced collection name for a tenant.

        Args:
            tenant_id: The tenant identifier.

        Returns:
            The collection name, e.g. ``tenant_acme_contracts``.
        """
        _validate_tenant_id(tenant_id)
        return f"tenant_{tenant_id}_contracts"

    def _collection(self, tenant_id: str) -> Collection:
        """Get-or-create the tenant's collection.

        Args:
            tenant_id: The tenant identifier.

        Returns:
            The Chroma :class:`Collection` for the tenant.
        """
        return self._client.get_or_create_collection(
            name=self.collection_name(tenant_id),
            embedding_function=self._embed,  # type: ignore[arg-type]
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

    def query(
        self,
        tenant_id: str,
        query_text: str,
        *,
        top_k: int = 10,
        document_ids: list[str] | None = None,
    ) -> list[RetrievedChunk]:
        """Run a similarity query within the tenant's collection.

        The tenant scoping is structural (separate collection) *and* enforced via
        metadata filtering — defence in depth.

        Args:
            tenant_id: The tenant identifier.
            query_text: The query string.
            top_k: Maximum number of chunks to return.
            document_ids: Optional document subset to restrict the search to.

        Returns:
            Matching chunks ordered by ascending distance.
        """
        collection = self._collection(tenant_id)
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
            where=where,
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
                    page_number=None if page in (-1, None) else int(page),
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
        logger.info("Deleted chunks for tenant=%s document=%s", tenant_id, document_id)
