import { json } from "@sveltejs/kit";
import { checkedStills } from "$lib/server/accuracy.js";

// Every "labels checked" snapshot, for scoring a candidate run against the
// reviewed stills before it touches the library
// (providers/annotation/score.py). Admin only, like everything under /admin.
export async function GET() {
  return json(
    { format: "hikari-label-checks-1", exported_at: new Date().toISOString(), checks: await checkedStills() },
    { headers: { "content-disposition": 'attachment; filename="label-checks.json"' } },
  );
}
