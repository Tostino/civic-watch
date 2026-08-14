import { ReviewScreen } from "@/components/admin/ReviewScreen";

/* Next 16: params and searchParams are Promises. */
type Props = {
  params: Promise<{ video: string }>;
  searchParams: Promise<{ name?: string; label?: string; sel?: string }>;
};

export default async function ReviewPage({ params, searchParams }: Props) {
  const [{ video }, q] = await Promise.all([params, searchParams]);
  return <ReviewScreen video={video} name={q.name} label={q.label} sel={q.sel} />;
}
