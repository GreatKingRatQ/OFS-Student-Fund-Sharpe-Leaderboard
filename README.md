# OFS Student Fund – Sharpe leaderboard

A web page, shared by link, that ranks each fund member's IBKR sub-account by Sharpe ratio. It updates itself every weekday night.

```
IBKR Flex Web Service ──▶ GitHub Actions (nightly, Python) ──▶ GitHub Pages
  daily NAV + deposits       Sharpe, return, vol, drawdown       https://<you>.github.io/<repo>/
```

- No server to run and nothing to pay for. GitHub runs the script and hosts the page.
- Your IBKR token stays in GitHub's encrypted secrets. It never goes in the code or on the page.
- The page shows return statistics only. It never shows balances or account numbers.

## What's in here

| Path | What it does |
|---|---|
| `members.csv` | Maps each IBKR sub-account number to a name and role. **You edit this.** |
| `manual_flows.csv` | Optional. Lists cash moves that IBKR doesn't report as deposits (see below). |
| `src/flex.py` | Calls the IBKR Flex Web Service and reads the XML it returns. |
| `src/metrics.py` | Flow-adjusted daily returns, annualised Sharpe, volatility and max drawdown. |
| `src/build.py` | Puts it all together and writes `site/index.html`. |
| `src/template.html` | The leaderboard page. |
| `.github/workflows/leaderboard.yml` | The nightly job. |
| `tests/` | Unit tests. They run before every build. |

Everything uses the Python standard library, so there is nothing to install.

## Setup (about 20 minutes)

### 1. Create the Flex Query in IBKR (using the master/advisor login)

1. In the Client Portal, open **Performance & Reports → Flex Queries** and click **+** next to *Activity Flex Query*.
2. Add these sections:
   - **Net Asset Value (NAV) in Base**: tick at least *Account ID*, *Report Date* and *Total*.
   - **Cash Transactions**: tick *Account ID*, *Type*, *Report Date*, *Amount*, *FX Rate To Base* and *Level of Detail*. Under options, select *Deposits & Withdrawals*.
3. Delivery configuration:
   - **Accounts**: every analyst sub-account. Create the query with the advisor/master account selected. A query made with a single account selected won't show up for the master account.
   - **Format**: XML. **Period**: Last 365 Calendar Days. **Date format**: yyyyMMdd.
4. Save it and note the **Query ID**.
5. Under **Settings → Flex Web Service**, turn the service on and generate a **token**. Choose the longest expiry and put the renewal date in the society calendar. The page stops updating when the token expires.

To check the query, run it once from the portal and download the XML. You should see one `EquitySummaryByReportDateInBase` row per account per day. If you only get one row per account, set **Breakout by Day** to *Yes* in the query's general configuration.

### 2. Fill in `members.csv`

Replace the placeholder `U0000001`… numbers with the real sub-account IDs. A member with no account yet can keep a blank ID; they appear under "Not ranked yet". Add surnames where two members share a first name, for example the two Josephs.

You can test with the XML you downloaded before going near GitHub:

```bash
python3 src/build.py --input path/to/flex_report.xml
open site/index.html
```

### 3. Put it on GitHub

1. Create a new **public** repository, e.g. `ofs-sharpe-leaderboard`, and upload this folder, including the hidden `.github` folder. A free GitHub account can only host Pages from a public repo. The account numbers in `members.csv` go into that repo, but they are not credentials and nobody can trade with them. If you'd rather keep them private, use GitHub Pro (free for students through the GitHub Student Developer Pack) and make the repo private.
2. **Settings → Secrets and variables → Actions**
   - Under *Secrets*, add `IBKR_FLEX_TOKEN` and `IBKR_FLEX_QUERY_ID`.
   - Under *Variables* (optional): `RISK_FREE_RATE` (default `0.04`, the Bank of England's 4.00% Bank Rate), `RISK_FREE_LABEL`, and `MIN_DAYS` (default `20`). If the sub-accounts are in USD, use a US T-bill yield instead.
3. **Settings → Pages → Build and deployment → Source: GitHub Actions.**
4. Under **Actions → Update Sharpe leaderboard → Run workflow**, run it once by hand. When it finishes, the run page shows the site link (`https://<username>.github.io/<repo>/`). That is the link to send to the analysts.

## How the numbers are worked out

- **Daily return** = NAV today ÷ (NAV yesterday + deposits − withdrawals since yesterday) − 1. Cash moves count from the start of the day, so new money is never treated as a gain. The day an account is first funded is left out.
- **Sharpe** = mean(daily return − daily risk-free) ÷ stdev(daily return − daily risk-free) × √252. The daily risk-free rate is (1 + annual rate)^(1/252) − 1.
- **Volatility** = stdev of daily returns × √252. **Return** and **max drawdown** come from the compounded daily returns for the chosen period.
- **Whole fund** = the capital-weighted average of members' daily returns.
- **Periods**: *Since inception* and *Last month* (the last 21 trading days). A member is ranked once they have `MIN_DAYS` days of returns (15 for the last month).

## Things to know

- **Internal transfers.** If the fund moves cash between sub-accounts and IBKR doesn't book it as a deposit or withdrawal, that day looks like a large gain or loss. The build log flags any day above ±25%. Add the move to `manual_flows.csv` (`date,account_id,amount`, with a positive amount for money in) and rerun.
- **Who can see it.** Anyone with the link can open it, and no login is needed. To restrict it to Oxford emails, you can put the same `site/` folder behind Cloudflare Pages with Cloudflare Access, which is free for up to 50 users.
- **Scheduled runs pause after 60 days with no commits.** GitHub disables cron jobs in quiet repos. Push any small change, or re-enable the job in the Actions tab, at the start of each term.
- **Short history.** Six weeks of daily data gives a noisy Sharpe ratio. The page says so, and the ranking settles as the term goes on.
- **Preview without IBKR:** `python3 src/build.py --demo` builds the page with invented numbers and a "sample data" banner.
