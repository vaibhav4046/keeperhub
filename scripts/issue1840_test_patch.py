from pathlib import Path

path = Path("tests/unit/submit-signed-solana.test.ts")
text = path.read_text(encoding="utf-8")


def replace_once(old: str, new: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected one match, got {count}: {old[:100]!r}")
    text = text.replace(old, new, 1)


replace_once(
'''  it("re-throws original error if duplicate transaction is not confirmed or found on-chain", async () => {
    mockConnection.sendRawTransaction.mockRejectedValue(
      new Error("already processed")
    );
    mockConnection.getSignatureStatuses.mockResolvedValue({
      value: [null],
    });

    await expect(
      submitSignedSolanaTransactionWithFailover(txBytes, mockManager, NO_DELAY)
    ).rejects.toThrow("already processed");

    expect(mockConnection.sendRawTransaction).toHaveBeenCalledTimes(1);
    expect(mockConnection.getSignatureStatuses).toHaveBeenCalledTimes(
      RECONCILE_ATTEMPTS
    );
  });''',
'''  it("preserves the deterministic signature when a send reply is lost and status stays unknown", async () => {
    mockConnection.sendRawTransaction.mockRejectedValue(
      new Error("already processed")
    );
    mockConnection.getSignatureStatuses.mockResolvedValue({ value: [null] });

    await expect(
      submitSignedSolanaTransactionWithFailover(txBytes, mockManager, NO_DELAY)
    ).rejects.toMatchObject({
      name: "OnChainPendingError",
      kind: "onchain-pending",
      transactionHash: expect.any(String),
    });

    expect(mockConnection.sendRawTransaction).toHaveBeenCalledTimes(1);
    expect(mockConnection.getSignatureStatuses).toHaveBeenCalledTimes(
      RECONCILE_ATTEMPTS
    );
  });'''
)

replace_once(
'''  it("reconciles on any broadcast error and rethrows when the tx never landed", async () => {
    // A non-duplicate error (timeout, or an RPC-side rejection). The signed
    // bytes are always reconcilable, so the status is checked; a null status
    // means the tx never landed, so the original error is rethrown.
    mockConnection.sendRawTransaction.mockRejectedValue(
      new Error("BlockhashNotFound")
    );
    mockConnection.getSignatureStatuses.mockResolvedValue({ value: [null] });

    await expect(
      submitSignedSolanaTransactionWithFailover(txBytes, mockManager, NO_DELAY)
    ).rejects.toThrow("BlockhashNotFound");

    expect(mockConnection.sendRawTransaction).toHaveBeenCalledTimes(1);
    expect(mockConnection.getSignatureStatuses).toHaveBeenCalledTimes(
      RECONCILE_ATTEMPTS
    );
  });''',
'''  it("fails closed with the deterministic signature when a broadcast error cannot be reconciled", async () => {
    mockConnection.sendRawTransaction.mockRejectedValue(
      new Error("BlockhashNotFound")
    );
    mockConnection.getSignatureStatuses.mockResolvedValue({ value: [null] });

    await expect(
      submitSignedSolanaTransactionWithFailover(txBytes, mockManager, NO_DELAY)
    ).rejects.toMatchObject({
      name: "OnChainPendingError",
      transactionHash: expect.any(String),
    });

    expect(mockConnection.sendRawTransaction).toHaveBeenCalledTimes(1);
    expect(mockConnection.getSignatureStatuses).toHaveBeenCalledTimes(
      RECONCILE_ATTEMPTS
    );
  });'''
)

replace_once(
'''  it("rethrows when the reconciled tx is confirmed but has an execution error", async () => {
    mockConnection.sendRawTransaction.mockRejectedValue(
      new Error("already been processed")
    );
    mockConnection.getSignatureStatuses.mockResolvedValue({
      value: [
        {
          confirmationStatus: "confirmed",
          err: { InstructionError: [0, "Custom"] },
        },
      ],
    });

    await expect(
      submitSignedSolanaTransactionWithFailover(txBytes, mockManager, NO_DELAY)
    ).rejects.toThrow("already been processed");

    // An explicit on-chain error is a final answer, so polling stops there
    // rather than burning the remaining attempts.
    expect(mockConnection.getSignatureStatuses).toHaveBeenCalledTimes(1);
  });''',
'''  it("returns the signature when reconciliation finds an on-chain execution error", async () => {
    mockConnection.sendRawTransaction.mockRejectedValue(
      new Error("already been processed")
    );
    mockConnection.getSignatureStatuses.mockResolvedValue({
      value: [
        {
          confirmationStatus: "confirmed",
          err: { InstructionError: [0, "Custom"] },
        },
      ],
    });

    const result = await submitSignedSolanaTransactionWithFailover(
      txBytes,
      mockManager,
      NO_DELAY
    );
    expect(result.signature).toBeDefined();
    // The adapter, which has the blockhash context, performs the authoritative
    // confirmation read and turns the chain error into OnChainRevertError.
    expect(mockConnection.getSignatureStatuses).toHaveBeenCalledTimes(1);
  });'''
)

replace_once(
'''  it("rethrows when the reconciled tx is only at 'processed' commitment", async () => {
    mockConnection.sendRawTransaction.mockRejectedValue(
      new Error("already been processed")
    );
    mockConnection.getSignatureStatuses.mockResolvedValue({
      value: [{ confirmationStatus: "processed", err: null }],
    });

    await expect(
      submitSignedSolanaTransactionWithFailover(txBytes, mockManager, NO_DELAY)
    ).rejects.toThrow("already been processed");
  });''',
'''  it("returns the signature as soon as reconciliation proves the transaction reached the network", async () => {
    mockConnection.sendRawTransaction.mockRejectedValue(
      new Error("already been processed")
    );
    mockConnection.getSignatureStatuses.mockResolvedValue({
      value: [{ confirmationStatus: "processed", err: null }],
    });

    const result = await submitSignedSolanaTransactionWithFailover(
      txBytes,
      mockManager,
      NO_DELAY
    );
    expect(result.signature).toBeDefined();
    expect(mockConnection.getSignatureStatuses).toHaveBeenCalledTimes(1);
  });'''
)

path.write_text(text, encoding="utf-8", newline="\n")
print("submit-signed-solana tests updated")
