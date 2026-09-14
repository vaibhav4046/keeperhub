import { describe, expect, it } from "vitest";
import { dispositionForExecutionOutcome } from "@/lib/idempotency-disposition";

describe("idempotency execution evidence", () => {
  it("releases a definite hashless pre-broadcast failure", () => {
    expect(dispositionForExecutionOutcome("failed", {})).toBe("release");
  });

  it("holds a sponsored submission whose hash is not available yet", () => {
    expect(dispositionForExecutionOutcome("failed", { sponsored: true })).toBe(
      "failed"
    );
  });

  it("releases a conclusive sponsored failure once its hash is known", () => {
    expect(
      dispositionForExecutionOutcome("failed", {
        sponsored: true,
        transactionHash: "0xdeadbeef",
      })
    ).toBe("release");
  });
});
