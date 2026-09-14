/** How a reserved idempotency record should settle once work returns. */
export type IdempotencyDisposition = "success" | "failed" | "release";

export type IdempotencyExecutionEvidence = {
  transactionHash?: string;
  sponsored?: boolean;
  broadcastAttempted?: boolean;
};

/**
 * Certainty, not success, decides whether a key is released.
 * Direct EVM writes derive their hash before broadcast, so a lost send reply
 * remains hash-bearing. Sponsored providers can additionally say submission
 * was attempted before a hash is available.
 */
export function dispositionForExecutionOutcome(
  status: "completed" | "failed" | "unconfirmed",
  evidence?: IdempotencyExecutionEvidence
): IdempotencyDisposition {
  if (status === "completed") {
    return "success";
  }
  if (status === "unconfirmed") {
    return "failed";
  }
  if (
    !evidence?.transactionHash &&
    (evidence?.broadcastAttempted === true || evidence?.sponsored === true)
  ) {
    return "failed";
  }
  return "release";
}
