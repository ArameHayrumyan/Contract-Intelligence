import { redirect } from "next/navigation";

/** Home route redirects to the upload workflow entrypoint. */
export default function HomePage() {
  redirect("/upload");
}
