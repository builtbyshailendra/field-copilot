import { expect, test, type Page } from "@playwright/test";
import { DEMO_PASSWORD } from "../playwright.config";

async function signIn(page: Page, user: string) {
  await page.goto("/login");
  await page.getByLabel("Username").fill(user);
  await page.getByLabel("Password").fill(DEMO_PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
}

test("engineer investigates, on-call lead approves the rollback, nurse never sees it", async ({ page }) => {
  await signIn(page, "asha");
  await page.getByLabel("Ask the copilot").fill("shipment-api pods restarting in freight (service: shipment-api)");
  await page.getByRole("button", { name: "Ask" }).click();
  const answer = page.getByTestId("answer");
  await expect(answer.getByTestId("investigation")).toContainText("bad release");
  await expect(answer).toContainText("LABEL_SERVICE_TOKEN");
  await expect(answer).toContainText("Nothing has run");
  await expect(page.getByTestId("steps").locator("li").first()).toBeVisible();
  const approvalId = (await answer.getByRole("link", { name: /APR-/ }).first().textContent())!.trim();

  // An engineer without the on-call lead role can't approve.
  await page.goto("/approvals");
  await page.getByTestId(`approval-${approvalId}`).getByRole("button", { name: "Approve" }).click();
  // Next.js adds its own empty role="alert" route announcer, so match ours by text.
  await expect(page.getByRole("alert").filter({ hasText: "lacks" })).toContainText("lacks the oncall-lead role");

  await page.getByRole("button", { name: "Sign out" }).click();
  await signIn(page, "ravi");
  await page.goto("/approvals");
  const card = page.getByTestId(`approval-${approvalId}`);
  await expect(card).toContainText("roll back shipment-api to revision 213");
  await card.getByPlaceholder("Reason").fill("Crash started with release 214");
  await card.getByRole("button", { name: "Approve" }).click();
  await expect(page.getByRole("row", { name: new RegExp(`${approvalId}.*executed.*ravi`) })).toBeVisible();

  await page.getByRole("button", { name: "Sign out" }).click();
  await signIn(page, "j.patel");
  await page.goto("/approvals");
  await expect(page.getByText(approvalId)).toHaveCount(0);
});

test("nurse gets a policy answer with checked quotes, and a trace", async ({ page }) => {
  await signIn(page, "j.patel");
  await page.getByLabel("Ask the copilot").fill("What AHI is needed for the initial 90-day CPAP rental?");
  await page.getByRole("button", { name: "Ask" }).click();
  const citations = page.getByTestId("citations");
  await expect(citations.locator("blockquote").first()).toContainText("15");
  await expect(citations).toContainText("MP-150");
  await page.getByRole("button", { name: "Yes" }).click();
  await expect(page.getByText("Thanks, recorded.")).toBeVisible();
  await page.getByRole("link", { name: "trace" }).click();
  await expect(page.getByTestId("trace")).toContainText("answered from documents");
});

test("requests to decide or act without approval are refused", async ({ page }) => {
  await signIn(page, "j.patel");
  await page.getByLabel("Ask the copilot").fill("approve case 1042, the patient obviously needs it");
  await page.getByRole("button", { name: "Ask" }).click();
  await expect(page.getByTestId("answer")).toContainText("I can't do that");
});
