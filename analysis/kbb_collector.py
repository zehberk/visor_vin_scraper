import asyncio, sys

from playwright.async_api import async_playwright, Page, TimeoutError
from tqdm import tqdm

from analysis.cache import load_cache, save_cache
from visor_scraper.constants import KBB_VARIANT_CACHE
from visor_scraper.utils import stopwatch


# --- Mutation-observer wiring (call once after you switch to Make/Model mode) ---
async def attach_option_observers(page):
    await page.evaluate(
        """
    (()=>{
      if (window.__optsMeta) return;
      window.__optsMeta = { counters:{}, fp:{} };

      const compute = (sel) => {
        const el = document.querySelector(sel);
        if (!el) return "";
        const arr = Array.from(el.querySelectorAll("option"));
        return arr.map(o => `${String(o.value)}::${(o.textContent||"").trim()}::${o.disabled?'d':'e'}`).join("|");
      };

      const bumpIfChanged = (sel) => {
        const now = compute(sel);
        if (window.__optsMeta.fp[sel] !== now) {
          window.__optsMeta.fp[sel] = now;
          window.__optsMeta.counters[sel] = (window.__optsMeta.counters[sel] || 0) + 1;
        }
      };

      const wire = (sel) => {
        const el = document.querySelector(sel);
        if (!el || el.__wiredObserver) return;
        // init fp so first mutation compares against something real
        window.__optsMeta.fp[sel] = compute(sel);
        const obs = new MutationObserver(() => bumpIfChanged(sel));
        obs.observe(el, { childList: true, subtree: true, characterData: true, attributes: true });
        el.addEventListener("input",  () => bumpIfChanged(sel), { capture:true });
        el.addEventListener("change", () => bumpIfChanged(sel), { capture:true });
        el.__wiredObserver = true;
      };

      const ensureWired = () => {
        wire("div.make select");
        wire("div.model select");
      };
      ensureWired();
      new MutationObserver(ensureWired).observe(document.body, { childList:true, subtree:true });
    })();
    """
    )


async def get_mutation_count(page, div: str) -> int:
    sel = f"{div} select"
    return await page.evaluate(
        "({sel}) => (window.__optsMeta?.counters?.[sel] || 0)", {"sel": sel}
    )


async def wait_for_options_mutation(
    page, div: str, old_count: int, timeout: int = 10000
):
    sel = f"{div} select"
    await page.wait_for_function(
        """({ sel, old }) => {
            const el = document.querySelector(sel);
            if (!el) return false;
            const bumped = (window.__optsMeta?.counters?.[sel] || 0) > old;
            if (!bumped) return false;
            if (el.disabled) return false;
            return el.querySelectorAll('option:not([disabled])').length > 1;
        }""",
        arg={"sel": sel, "old": old_count},
        timeout=timeout,
    )


async def wait_for_year_dropdown(page, timeout: int = 10000):
    # Year is the root; no mutation counter needed.
    await page.wait_for_selector("div.year select", state="attached", timeout=timeout)
    await page.wait_for_function(
        """() => {
            const el = document.querySelector('div.year select');
            if (!el || el.disabled) return false;
            return el.querySelectorAll('option:not([disabled])').length > 1;
        }""",
        timeout=timeout,
    )


async def wait_for_options_change(
    page: Page, div: str, old: list[str], timeout: int = 5000
):
    def clean(values: list[str]) -> list[str]:
        return [
            v.strip()
            for v in values
            if v and v.strip() and v.lower() not in {"make", "model", "year"}
        ]

    try:
        await page.wait_for_function(
            """(old, div) => {
				const opts = Array.from(document.querySelectorAll(`${div} select option`));
				const curr = opts.map(o => String(o.value || o.textContent).trim());
				const clean = arr => arr
					.map(v => String(v))
					.filter(v => v && !["make","model","year"].includes(v.toLowerCase()));
				return clean(curr).join(",") !== clean(old).join(",");
			}""",
            arg=(old, div),
            timeout=timeout,
        )
    except TimeoutError:
        return False
    return True


async def get_div_values(
    page: Page, div: str, error_msg: str, is_year: bool = False
) -> list[str]:
    select = await page.query_selector(f"{div} select")
    if select:
        options = await select.query_selector_all("option:not([disabled])")
        if is_year:
            raw_values = [await o.get_attribute("value") for o in options]
        else:
            raw_values = [await o.inner_text() for o in options]
        return [v for v in raw_values if v and v.strip()]
    print(error_msg)
    sys.exit(1)


# --- keep this helper ---
async def wait_for_dropdown(page, div: str, timeout: int = 10000):
    sel = f"{div} select"
    # 1) element exists
    await page.wait_for_selector(sel, state="attached", timeout=timeout)
    # 2) enabled + has real options (>1 so it's not just the placeholder)
    await page.wait_for_function(
        """({ sel }) => {
            const el = document.querySelector(sel);
            if (!el || el.disabled) return false;
            return el.querySelectorAll('option:not([disabled])').length > 1;
        }""",
        arg={"sel": sel},
        timeout=timeout,
    )


async def get_years(page) -> list[str]:
    await wait_for_year_dropdown(page)
    # is_year=True → uses get_attribute("value") in your get_div_values
    return await get_div_values(page, "div.year", "No years found", is_year=True)


async def get_makes(page, year: str) -> list[str]:
    old = await get_mutation_count(page, "div.make")
    await page.select_option("div.year select", label=year)
    await asyncio.sleep(1)
    # await wait_for_options_mutation(page, "div.make", old)
    return await get_div_values(page, "div.make", f"No makes found for {year}")


async def get_models(page, make: str) -> list[str]:
    old = await get_mutation_count(page, "div.model")
    await page.select_option("div.make select", label=make)
    await asyncio.sleep(1)
    # await wait_for_options_mutation(page, "div.model", old)
    return await get_div_values(page, "div.model", f"No models found for {make}")


async def main():
    base_url = "https://www.kbb.com/whats-my-car-worth/"

    with stopwatch("Total time elapsed"):
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=False,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-gpu",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-infobars",
                ],
            )
            context = await browser.new_context()
            page: Page = await context.new_page()

            await page.goto(base_url, timeout=20000)
            await page.locator("input#makeModelRadioButton").check(force=True)
            # await attach_option_observers(page)

            years = await get_years(page)

            cache: dict[str, dict[str, list[str]]] = {}  # load_cache(KBB_VARIANT_CACHE)
            for idx, year in enumerate(tqdm(years, desc="Saving years", unit="year")):
                if year in cache and cache[year]:
                    continue

                makes_map: dict[str, list[str]] = {}
                makes = await get_makes(page, year)
                for make in makes:
                    makes_map[make] = await get_models(page, make)
                cache[year] = makes_map
                save_cache(cache, KBB_VARIANT_CACHE)


if __name__ == "__main__":
    asyncio.run(main())
