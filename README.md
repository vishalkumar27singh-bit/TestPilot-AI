# TestPilot AI — Automated Testing Agent

Enter a website URL → TestPilot validates it safely, analyzes the page, generates test cases, executes them
(Selenium UI tests, link/image/form/responsive checks, API tests), captures screenshots, explains failures,
files bug reports, stores everything in SQLite and exports PDF / CSV / JSON / HTML reports.

Works **without any AI key** (rule-based engine). Add a key to unlock AI test generation, failure explanations and summaries.

> **After updating, fully restart Streamlit** (Ctrl+C in the terminal, then `streamlit run app.py`) and check the sidebar shows the new version
> (v1.0.3). A running Streamlit server keeps old modules in memory, so an un-restarted dashboard can still show old behaviour.

## Setup on macOS (Apple Silicon, Terminal)

```bash
# 1. Extract
cd ~/Downloads && unzip TestPilot_AI.zip && cd TestPilot-AI

# 2. Create an environment  (pick ONE)
python3 -m venv .venv && source .venv/bin/activate          # plain Python 3.11+
# conda create -n testpilot python=3.11 -y && conda activate testpilot   # Anaconda alternative

# 3. Install dependencies
pip install --upgrade pip && pip install -r requirements.txt

# 4. (Optional) enable AI - local keys go in .env (or .streamlit/secrets.toml, see .streamlit/secrets.toml.example)
cp .env.example .env && open -e .env     # paste ANTHROPIC_API_KEY=... or OPENAI_API_KEY=...

# 5. Start the dashboard
streamlit run app.py                     # opens http://localhost:8501

# 6. Run the project's own test suite
pytest
```

**Browser testing needs Google Chrome** installed on macOS (https://www.google.com/chrome/). Selenium 4 downloads a matching ChromeDriver
automatically on first use (needs internet once). If Chrome/Selenium is unavailable, TestPilot falls back to the *static HTTP engine*
(no real browser: no screenshots, no layout/console checks) and says so in the log. `Settings → Environment check` shows what is installed.

## Deploying to Streamlit Community Cloud

1. **Push the project root to GitHub** so `app.py`, `requirements.txt` and `packages.txt` sit at the *top level* of the repository.
   `.env`, `.streamlit/secrets.toml`, databases and screenshots are git-ignored; check `git status` shows no secrets before you push.
2. **System packages:** `packages.txt` (included) makes Cloud `apt-get install` `chromium` and `chromium-driver`. TestPilot finds
   `/usr/bin/chromium` and `/usr/bin/chromedriver` automatically and launches them headless with `--no-sandbox --disable-dev-shm-usage`.
   Chromium and chromedriver come from the same Debian package, so their versions match. (If you ever change this file, reboot the app.)
3. **Create the app** at https://share.streamlit.io: pick the repo/branch, set **Main file path** to `app.py`, and under
   *Advanced settings* choose **Python 3.11 or 3.12**.
4. **Secrets (optional, enables AI):** App → *Settings → Secrets*, paste top-level TOML (see `.streamlit/secrets.toml.example`):
   ```toml
   ANTHROPIC_API_KEY = "sk-ant-..."
   # or OPENAI_API_KEY = "sk-..."
   ```
   Lookup order is: environment / `.env` first, then `st.secrets`. Do not put keys in the repo. Without any key the rule-based engine is used.
5. **Deploy.** Open *Settings → Environment check* in the app: Selenium and Chrome should show **OK**.

**What differs on Cloud**
* The browser is a Linux Chromium in a container; the macOS Chrome path is not used. Override with `CHROME_BINARY` / `CHROMEDRIVER_PATH` secrets only if needed.
* The bundled sample site and "Allow localhost" are for local use: on Cloud, `localhost` is the Cloud container, not your laptop. Test public sites you have permission to test.
* The filesystem is **ephemeral**: SQLite history and screenshots are lost when the app restarts or sleeps. Download reports you want to keep.
* Memory is limited; a very heavy site may make Chromium crash. The run then falls back to the static engine instead of failing.
* If Chromium or Selenium cannot start, TestPilot **does not fail**: the log and a warning banner say why, browser-only checks (screenshots, console errors, real layout) are reported SKIPPED, and all HTTP-based checks still run.

## Real browser testing (Selenium + Chrome)

TestPilot drives a real Chrome: it renders JavaScript, resizes the viewport (exactly, via Chrome DevTools emulation), takes screenshots,
reads the browser console, clicks links and safe buttons, and submits GET search forms. Every result carries an **Engine** label:

| Engine | Meaning |
|---|---|
| **Browser** | The check was really executed in Chrome. |
| **Static/HTTP** | HTML/HTTP analysis (broken links, alt text, viewport meta tag, ...). Never presented as a browser test. |
| **Not run** | SKIPPED. Typically needs a browser that was not available; the reason is in the result. |

**Setup on a MacBook (Apple Silicon or Intel):**
```bash
cd TestPilot-AI
source .venv/bin/activate                    # or: conda activate testpilot   (use the SAME environment every time)
python -m pip install -r requirements.txt    # installs Selenium into THIS Python
python -c "import selenium; print(selenium.__version__)"
python selfcheck_browser.py                  # launches real Chrome against the bundled sample site and verifies everything
python -m streamlit run app.py               # 'python -m' guarantees Streamlit uses the Python that has Selenium
```
Requirements: Google Chrome in `/Applications` and internet access the first time (Selenium Manager downloads the matching ARM64
chromedriver automatically; nothing to install by hand). If you have an old `chromedriver` on your PATH (e.g. from Homebrew) and see a version
mismatch, run `brew uninstall chromedriver`. Use `CHROME_BINARY` / `CHROMEDRIVER_PATH` (in `.env`) only for non-standard locations.
**Settings -> Browser diagnostics** shows which Python is running, the Selenium version, the Chrome version and the exact install command.

"Selenium is not installed" in a report almost always means Streamlit was started from a different Python than the one you ran `pip install` in.

**Safety of clicking:** only standalone buttons are clicked (never inside a `<form>`, never disabled/unnamed ones, never labels matching
delete/remove/pay/buy/order/send/submit/post/save/confirm/cancel/download/accept/...), at most 5, and internal links only (at most 3). After every click the
URL is re-checked against the SSRF rules, extra tabs are closed and the page is reloaded. A page can still have side effects you did not expect,
so only test sites you own or may test (untick "Click safe buttons" to disable button clicking).

**Tests:** `pytest` runs everything. 12 tests in `tests/test_browser_real.py` need real Chrome: they are SKIPPED with a reason without it (never passed).
`tests/test_browser_simulated.py` checks the browser *logic* with a simulated driver and is clearly not a Chrome verification.

## Testing any website

TestPilot has no built-in target list: you type any URL (a bare `my-app.example.com` is upgraded to `https://`) and the tests are
generated from what is detected on *that* page. A site with no navigation gets no navigation tests; a site with forms gets form tests.
Each run is independent, so one site failing never affects another (`core.test_executor.run_websites([...])` runs several in a row).

Generic handling of real-world behaviour (all still subject to the SSRF rules, re-checked on every redirect hop):
* **Redirects are never a reason to call a site "unavailable":** up to 10 hops are followed (20 on a retry with browser-like headers). If an HTTP client
  still cannot reach a final page (loops, endless or cookie/JavaScript-driven handshakes) but the server *did* answer, the site is reported **reachable**
  with its last status/URL; the browser engine then follows the redirects itself, and without a browser only truthful checks run (the rest are SKIPPED,
  never failed). Real failures (DNS, refused, timeout, TLS, HTTP 4xx/5xx, blocked/private targets) are still reported as unavailable.
* **Redirects:** followed with a per-request cookie jar (session/auth hand-offs work), loops reported as loops, credentials
  (`Authorization`, `Cookie`, API-key headers) dropped when a redirect leaves the original origin, final URL and redirect chain shown.
* **Clear failure messages:** 401/403 (login or bot protection), 404, 429, 5xx, timeouts, TLS problems, connection refused, non-HTML URLs.
* **Login walls, CAPTCHA/bot-protection pages, "app is asleep" pages, maintenance pages:** detected heuristically and reported as notes
  (they never silently pass as healthy results).
* **JavaScript-rendered apps (Streamlit, React, Vue...):** with Chrome, TestPilot waits for the DOM to stop changing (up to
  `RENDER_WAIT_SECONDS`, default 8) before analyzing. In static-HTTP fallback mode these pages look empty, and the report says so.

## Try it on the bundled sample site

```bash
python sample_site/serve.py          # terminal 2: http://127.0.0.1:8000  (contains deliberate bugs + a demo JSON API)
```
In the app: **Settings → enable "Allow localhost"** → Save → New Test Run → `http://127.0.0.1:8000/index.html`.
Expected findings: a 404 nav link, a missing image, an image without alt text, an unlabeled icon button and
(with Chrome) horizontal overflow on mobile. API demo endpoints: `/api/items`, `/api/health`, `/api/broken` (invalid JSON), `/api/slow` (2.5 s).

## What each page does

| Page | Purpose |
|---|---|
| Overview | Hero + URL box, live logs, KPI cards, pass/fail, history, severity and category charts, recent sessions |
| New Test Run | Full run form: engine, AI toggle, pytest mode, test categories, live progress + results |
| UI Testing | Analyze a page, inspect discovered elements, preview the generated plan, then run it |
| API Testing | GET/POST/PUT/DELETE, headers, JSON body editor, expectations; destructive methods need explicit confirmation |
| History | Open, compare (regressions/fixed) and delete (with confirmation) stored runs |
| Bug Reports | Auto-created bugs (ID, run, steps, expected/actual, screenshot, fix); status tracking; CSV export |
| Screenshots | Gallery with downloads |
| Settings | AI status, safety switches, timeouts, environment check |

## Architecture

```
app.py                     Streamlit shell (sidebar nav, styling)
views/                     One module per page + shared components
core/safety.py             URL validation, SSRF blocking, redirect re-validation, size/timeout caps
core/website_analyzer.py   HTML → SiteProfile (title, headings, nav, buttons, inputs, forms, images, features)
core/test_generator.py     Rule-based tests + AI proposals validated against a whitelist
core/test_executor.py      The 12-step workflow, live callbacks, error isolation per test
core/failure_analyzer.py   Rule + AI failure explanations, bug-report builder
core/ai_engine.py          Anthropic / OpenAI-compatible client via plain `requests` (keys via get_secret: env/.env, then st.secrets)
testing/                   selfcheck_browser.py (root), browser_config (macOS vs Linux/Cloud launch options), ui_tests, link_tests, responsive_tests, api_tests, registry (the action whitelist), pytest_bridge
database/                  schema.sql + db_manager.py (SQLite: runs, test_cases, bugs, screenshots)
reports/report_generator.py  PDF (ReportLab) / CSV / JSON / HTML
sample_site/               Demo site + JSON API with planted defects
tests/                     pytest suite (102 tests, 12 of which need real Chrome; tests/multisite.py simulates 17 different websites)
```

## Security model

* **SSRF protection:** only http/https; credentials in URLs rejected; hostnames resolved and *every* resolved IP must be public.
  Loopback, RFC1918, link-local (cloud metadata 169.254.169.254), multicast, reserved, IPv4-mapped IPv6 and decimal-IP tricks are blocked.
  Redirects are followed manually and each hop is re-validated. "Allow localhost" is an explicit opt-in that still blocks private ranges.
* **No AI code execution:** the AI can only *propose* declarative tests that name an action from `testing/registry.py` with whitelisted,
  range-checked parameters. Anything else is discarded. Scraped page text sent to the AI is flagged as untrusted.
* **Safe-by-default testing:** buttons are inspected, not clicked; forms are inspected, not submitted; only GET search forms are submitted.
  POST/PUT/DELETE API calls require an explicit confirmation checkbox; the error probe only ever sends GET.
* **Timeouts and caps** on every request (10 s default), 5 MB response cap, per-test exception isolation.
* **Secrets** live in `.env` or Streamlit secrets (both git-ignored / never in the repo); keys are never logged, displayed, or included in error messages.
* Test only sites you own or have written permission to test.

## Known limitations

* Pages behind a login, CAPTCHA or aggressive bot protection can't be tested; TestPilot reports this rather than guessing.
* A sleeping hosted app (e.g. on a free tier) must be woken in a browser first.

* DNS-rebinding TOCTOU: the hostname is validated, then resolved again by the HTTP client/browser. Don't test untrusted sites.
* Selenium's browser follows redirects/subresources on its own; the final URL is re-validated, but sub-requests the page makes are not.
* Static HTTP fallback cannot check real layout, console errors or take screenshots (those tests are reported SKIPPED, never faked).
* JavaScript-heavy single-page apps are analyzed after the browser renders them, but only the landing page is analyzed (no crawling).
* The AI layer was implemented against the public Anthropic/OpenAI HTTP APIs; set `AI_MODEL` if your account uses a different model name.
* "Run through pytest" executes every generated test as a parametrized pytest case inside the app process.
