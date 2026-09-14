import { describe, expect, it } from "vitest";
import { dispositionForExecutionOutcome } from "@/lib/idempotency-disposition";

describe("idempotency disposition execution evidence", () => {
  it("fails closed when a failed execution has no broadcast evidence", () => {
    expect(dispositionForExecutionOutcome("failed")).toBe("failed");
  });

  it("releases only explicit pre-broadcast failures", () => {
    expect(
      dispositionForExecutionOutcome("failed", { broadcastAttempted: false })
    ).toBe("release");
  });

  it("holds hashless attempted sends", () => {
    expect(
      dispositionForExecutionOutcome("failed", { broadcastAttempted: true })
    ).toBe("failed");
  });

  it("holds hashless sponsored sends", () => {
    expect(dispositionForExecutionOutcome("failed", { sponsored: true })).toBe(
      "failed"
    );
  });

  it("releases a terminal reconciled failure carrying a transaction hash", () => {
    expect(
      dispositionForExecutionOutcome("failed", {
        transactionHash: `0x${"11".repeat(32)}`,
        broadcastAttempted: true,
      })
    ).toBe("release");
  });

  it("always holds unconfirmed outcomes", () => {
    expect(
      dispositionForExecutionOutcome("unconfirmed", {
        broadcastAttempted: false,
      })
    ).toBe("failed");
  });
});
