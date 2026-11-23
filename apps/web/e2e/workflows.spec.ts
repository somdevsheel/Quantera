import { expect, test, type Page } from "@playwright/test";

const PASSWORD = "correct horse battery staple";

async function signUp(page: Page, name = "E2E User") {
  const email = `e2e-${Date.now()}-${Math.floor(Math.random() * 1e6)}@example.com`;
  await page.goto("/register");
  await page.getByLabel("Full name").fill(name);
  await page.getByLabel("Email", { exact: true }).fill(email);
  await page.getByLabel("Password", { exact: true }).fill(PASSWORD);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
  return email;
}

test("sign-in guard, registration, sign out and sign in", async ({ page }) => {
  await page.goto("/portfolios");
  await expect(page).toHaveURL(/\/login\?next=%2Fportfolios/);
  const email = await signUp(page);
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login/);
  await page.getByLabel("Email", { exact: true }).fill(email);
  await page.getByLabel("Password", { exact: true }).fill("wrong password here");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.locator(".form-error")).toContainText("incorrect");
  await page.getByLabel("Password", { exact: true }).fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
});

test("dashboard shows real index data with freshness", async ({ page }) => {
  await signUp(page);
  await expect(page.getByText("Nifty 50", { exact: true })).toBeVisible();
  await expect(page.getByText(/lake release/).first()).toBeVisible();
  await expect(page.getByText(/(FRESH|STALE)/).first()).toBeVisible();
});

test("search opens the research page with honest section states", async ({ page }) => {
  await signUp(page);
  await page.getByLabel("Search stocks").fill("TCS");
  await page.getByRole("listbox").getByText("TCS", { exact: true }).first().click();
  await expect(page).toHaveURL(/\/stocks\/XNSE\/TCS/);
  await expect(page.getByRole("heading", { name: /TCS/ })).toBeVisible();
  await page.getByRole("tab", { name: "Technical" }).click();
  await expect(page.getByRole("img", { name: /Price with moving averages/ })).toBeVisible();
  await page.getByRole("tab", { name: "Options" }).click();
  await expect(page.getByText("UNSUPPORTED")).toBeVisible();
  await page.getByRole("tab", { name: "Forecast" }).click();
  await expect(page.getByText("MODEL UNAVAILABLE")).toBeVisible();
});

test("scan NIFTY 50 end to end and filter results", async ({ page }) => {
  await signUp(page);
  await page.goto("/scanner");
  await page.getByLabel("Universe").selectOption("index:NIFTY 50");
  await page.getByRole("button", { name: "Run scan" }).click();
  await expect(page).toHaveURL(/\/scanner\/[0-9a-f-]+$/);
  await expect(page.getByText("SUCCEEDED")).toBeVisible({ timeout: 90_000 });
  await expect(page.getByText(/50 stocks/)).toBeVisible();
  await page.getByPlaceholder("Filter symbol / name").fill("TCS");
  await expect(page.getByRole("link", { name: "TCS", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Export CSV" })).toHaveAttribute("href", /export\.csv$/);
});

test("portfolio ledger, holdings and paper order", async ({ page }) => {
  await signUp(page);
  await page.goto("/portfolios");
  await page.getByLabel("Name").fill("Paper book");
  await page.getByLabel(/Paper portfolio/).check();
  await page.getByRole("button", { name: "Create" }).click();
  await page.getByRole("link", { name: "Paper book" }).click();
  await page.getByRole("tab", { name: "Add transactions" }).click();
  await page.getByLabel("Date").first().fill("2024-12-02");
  await page.getByLabel("Type").selectOption("DEPOSIT");
  await page.getByLabel("Amount (₹)").fill("1000000");
  await page.getByRole("button", { name: "Add", exact: true }).click();
  await expect(page.getByText("₹10,00,000").first()).toBeVisible();

  await page.getByRole("link", { name: "Paper orders" }).click();
  await page.getByLabel("Symbol (NSE)").fill("INFY");
  await page.getByLabel("Quantity").fill("10");
  await page.getByLabel(/Placed on/).fill("2025-01-02");
  await page.getByRole("button", { name: "Place paper order" }).click();
  await expect(page.getByText(/accepted \(WORKING\)/)).toBeVisible();
  await page.getByRole("button", { name: "Simulate fills" }).click();
  await expect(page.getByText(/processed 1 fill/)).toBeVisible();
  await page.getByRole("button", { name: "Reconcile" }).click();
  await expect(page.getByText("RECONCILED")).toBeVisible();
});

test("trade plan preview shows the four price fields", async ({ page }) => {
  await signUp(page);
  await page.goto("/plans?symbol=INFY");
  await page.getByRole("button", { name: "Preview levels" }).click();
  for (const label of ["Current price", "Stop loss", "Target price", "Trailing stop loss"]) {
    await expect(page.locator(".level > span", { hasText: new RegExp(`^${label}$`) })).toBeVisible();
  }
  await page.getByRole("button", { name: "Save plan" }).click();
  await expect(page.locator("table").getByRole("link", { name: "INFY" })).toBeVisible();
});

test("strategy library is honest", async ({ page }) => {
  await signUp(page);
  await page.goto("/strategies");
  await expect(page.getByText(/All 20/)).toBeVisible();
  await expect(page.locator(".badge", { hasText: /^(APPROVED|PAPER TRADING|BACKTEST PASSED)$/ })).toHaveCount(0);
  await expect(page.locator(".badge", { hasText: /^RESEARCH$/ }).first()).toBeVisible();
});

test("settings: MFA setup flow is reachable and sessions listed", async ({ page }) => {
  await signUp(page);
  await page.goto("/settings");
  await page.getByRole("button", { name: "Set up" }).click();
  await expect(page.getByText("Secret key", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Signed-in sessions" })).toBeVisible();
});

test("mobile navigation works @mobile", async ({ page }) => {
  await signUp(page);
  await page.getByRole("button", { name: "Menu" }).click();
  await page.getByRole("link", { name: "Alerts", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Alerts" })).toBeVisible();
});
