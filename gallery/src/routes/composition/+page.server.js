import { allVisibleStills } from "$lib/server/catalog.js";
import { techniques } from "$lib/server/composition.js";

// The technique index. Tiles link into Explore (/?<family>=<value>), which
// owns results and narrowing.
export async function load() {
  const stills = await allVisibleStills();
  return { families: techniques(stills), total: stills.length };
}
