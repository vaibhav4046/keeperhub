from pathlib import Path

ROOT = Path(".")

def load(path: str) -> str:
    return (ROOT / path).read_text()

def save(path: str, text: str) -> None:
    (ROOT / path).write_text(text)

def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly 1 match, found {count}")
    return text.replace(old, new, 1)

def replace_next_after(text: str, anchor: str, old: str, new: str, label: str) -> str:
    anchor_pos = text.find(anchor)
    if anchor_pos < 0:
        raise RuntimeError(f"{label}: anchor not found")
    pos = text.find(old, anchor_pos)
    if pos < 0:
        raise RuntimeError(f"{label}: target not found after anchor")
    return text[:pos] + new + text[pos + len(old):]

path = "lib/rpc/providers/index.ts"
text = load(path)
old = '''  return "rpc_error";
}

export type RpcProviderConfig = {'''
new = '''  return "rpc_error";
}

function isConnectionRefusal(error: unknown): boolean {
  const message =
    error instanceof Error ? error.message.toLowerCase() : String(error).toLowerCase();
  return (
    message.includes("econnrefused") || message.includes("connection refused")
  );
}

export type RpcProviderConfig = {'''
text = replace_once(text, old, new, "provider: add connection-refusal helper")
for endpoint in ("fallback", "primary"):
    old = f'{{ endpoint: "{endpoint}", transport: {endpoint}Result.transport }},'
    new = f'''{{
              endpoint: "{endpoint}",
              transport: {endpoint}Result.transport,
              allAttemptsConnectionRefused:
                {endpoint}Result.allAttemptsConnectionRefused,
            }},'''
    if old not in text:
        raise RuntimeError(f"provider: missing {endpoint} failure tuple")
    text = text.replace(old, new)
old = '''[{ endpoint: "primary", transport: primaryResult.transport }]'''
new = '''[
        {
          endpoint: "primary",
          transport: primaryResult.transport,
          allAttemptsConnectionRefused:
            primaryResult.allAttemptsConnectionRefused,
        },
      ]'''
text = replace_once(text, old, new, "provider: primary-only failure tuple")
old = '''    failures: readonly {
      endpoint: "primary" | "fallback";
      transport?: boolean;
    }[]
  ): Error {
    const redacted = redactAllUrls(message);
    const allOnTheRelay = failures.every(
'''
new = '''    failures: readonly {
      endpoint: "primary" | "fallback";
      transport?: boolean;
      allAttemptsConnectionRefused?: boolean;
    }[]
  ): Error {
    const redacted = redactAllUrls(message);
    const allAttemptsConnectionRefused =
      failures.length > 0 &&
      failures.every(
        (failure) => failure.allAttemptsConnectionRefused === true
      );
    const allOnTheRelay = failures.every(
'''
text = replace_once(text, old, new, "provider: failoverError failure metadata")
old = '''    return allOnTheRelay
      ? new RpcRelayTransportError(redacted)
      : new Error(redacted);
  }
'''
new = '''    const result = allOnTheRelay
      ? new RpcRelayTransportError(redacted)
      : new Error(redacted);
    // A write-broadcast classifier must know whether *every retry attempt*
    // was refused. The rendered message only contains each endpoint's final
    // error, so text alone can turn "timeout, then refused" into "never sent".
    // Duck-typed by submit-signed.ts to avoid coupling callers to this class.
    Object.assign(result, { allAttemptsConnectionRefused });
    return result;
  }
'''
text = replace_once(text, old, new, "provider: attach aggregate refusal metadata")
old = '''    /** Whether the last attempt failed on transport rather than on an answer. */
    transport?: boolean;
  }> {
    let lastError: Error | undefined;

    for (let attempt = 0; attempt < maxRetries; attempt++) {
'''
new = '''    /** Whether the last attempt failed on transport rather than on an answer. */
    transport?: boolean;
    /** True only when every retry attempt ended in a connection refusal. */
    allAttemptsConnectionRefused?: boolean;
  }> {
    let lastError: Error | undefined;
    let attemptCount = 0;
    let allAttemptsConnectionRefused = true;

    for (let attempt = 0; attempt < maxRetries; attempt++) {
'''
text = replace_once(text, old, new, "provider: tryProvider return metadata")
old = '''        lastError = error instanceof Error ? error : new Error(String(error));
        this.recordFailure(providerType, operationType);
'''
new = '''        lastError = error instanceof Error ? error : new Error(String(error));
        attemptCount += 1;
        allAttemptsConnectionRefused =
          allAttemptsConnectionRefused && isConnectionRefusal(error);
        this.recordFailure(providerType, operationType);
'''
text = replace_once(text, old, new, "provider: aggregate retry attempts")
old = '''      error: scrubRpcUrls(lastError?.message ?? "") || "Unknown error",
      transport: isTransportFailure(lastError),
    };
'''
new = '''      error: scrubRpcUrls(lastError?.message ?? "") || "Unknown error",
      transport: isTransportFailure(lastError),
      allAttemptsConnectionRefused:
        attemptCount > 0 && allAttemptsConnectionRefused,
    };
'''
text = replace_once(text, old, new, "provider: return retry aggregate")
save(path, text)

path = "lib/web3/submit-signed.ts"
text = load(path)
old = '''export function isDefinitelyPreBroadcastNetworkError(error: unknown): boolean {
  const message = errorMessage(error).toLowerCase();
'''
new = '''export function isDefinitelyPreBroadcastNetworkError(error: unknown): boolean {
  if (error instanceof Error && "allAttemptsConnectionRefused" in error) {
    const aggregate = (
      error as Error & { allAttemptsConnectionRefused?: unknown }
    ).allAttemptsConnectionRefused;
    if (typeof aggregate === "boolean") {
      return aggregate;
    }
  }

  const message = errorMessage(error).toLowerCase();
'''
text = replace_once(text, old, new, "submit-signed: structured retry-history override")
save(path, text)

path = "plugins/tempo/steps/tempo-tx-core.ts"
text = load(path)
old = '''    // Node envelope rejections are terminal, not pending: the node read the
    // envelope and refused it (the payer cannot cover it, or the envelope
    // fails intrinsic validation), so nothing was broadcast. Wrapping one in
    // OnChainPendingError would park the row in `broadcast` with a hash that
    // is not on chain, and the reconcile sweep reads a not-found hash as
    // pending forever.
    if (isNodeEnvelopeRejection(message)) {
      throw error;
    }
    // Provenance is established here: this catch wraps only the send call,
    // so a refusal message can only have come from the broadcast itself.
    if (isDefinitelyPreBroadcastNetworkError(error)) {
      throw error;
    }
    throw new OnChainPendingError({
      message: `Tempo transaction send outcome could not be determined (${message})`,
      transactionHash: actualHash,
    });
'''
new = '''    // A definite envelope rejection or an all-attempts connection refusal is
    // terminal only if the deterministic signed hash is also absent. A prior
    // retry may have reached a node before a later refusal masked it, so probe
    // the hash exactly as the EVM signed-send path does before releasing the
    // idempotency key.
    const looksDefinitelyPreBroadcast =
      isNodeEnvelopeRejection(message) ||
      isDefinitelyPreBroadcastNetworkError(error);
    if (looksDefinitelyPreBroadcast) {
      let visible: ethers.TransactionResponse | null;
      try {
        visible = await rpcManager.executeWithFailover(
          (provider) => provider.getTransaction(actualHash),
          "read"
        );
      } catch (lookupError) {
        throw new OnChainPendingError({
          message: `Tempo transaction send outcome could not be determined (${lookupError instanceof Error ? lookupError.message : String(lookupError)})`,
          transactionHash: actualHash,
        });
      }

      if (visible) {
        hash = actualHash;
      } else {
        throw error;
      }
    } else {
      throw new OnChainPendingError({
        message: `Tempo transaction send outcome could not be determined (${message})`,
        transactionHash: actualHash,
      });
    }
'''
text = replace_once(text, old, new, "tempo: reconcile before terminal refusal")
save(path, text)

for path, read_old, read_new, anchor in [
    (
        "plugins/web3/steps/transfer-token-core.ts",
        '''      const [decimals, symbol, balance] =
        await rpcManager.executeWithFailover((p) => {
          const tokenContract = new ethers.Contract(tokenAddress, ERC20_ABI, p);
          return Promise.all([
            tokenContract.decimals() as Promise<bigint>,
            tokenContract.symbol() as Promise<string>,
            tokenContract.balanceOf(tokenHolderAddress) as Promise<bigint>,
          ]);
        });
''',
        '''      let decimals: bigint;
      let symbol: string;
      let balance: bigint;
      try {
        [decimals, symbol, balance] = await rpcManager.executeWithFailover((p) => {
          const tokenContract = new ethers.Contract(tokenAddress, ERC20_ABI, p);
          return Promise.all([
            tokenContract.decimals() as Promise<bigint>,
            tokenContract.symbol() as Promise<string>,
            tokenContract.balanceOf(tokenHolderAddress) as Promise<bigint>,
          ]);
        });
      } catch (error) {
        return {
          success: false,
          error: `Failed to read token metadata or balance: ${getErrorMessage(error)}`,
        };
      }
''',
        "const tokenHolderAddress =",
    ),
    (
        "plugins/web3/steps/approve-token-core.ts",
        '''      // Get token decimals and symbol via failover
      const [decimals, symbol] = await rpcManager.executeWithFailover(
        (p) => {
          const tokenContract = new ethers.Contract(tokenAddress, ERC20_ABI, p);
          return Promise.all([
            tokenContract.decimals() as Promise<bigint>,
            tokenContract.symbol() as Promise<string>,
          ]);
        }
      );
''',
        '''      // Get token decimals and symbol via failover. This is a read-only
      // preflight: a decode/RPC failure here proves no broadcast was attempted.
      let decimals: bigint;
      let symbol: string;
      try {
        [decimals, symbol] = await rpcManager.executeWithFailover((p) => {
          const tokenContract = new ethers.Contract(tokenAddress, ERC20_ABI, p);
          return Promise.all([
            tokenContract.decimals() as Promise<bigint>,
            tokenContract.symbol() as Promise<string>,
          ]);
        });
      } catch (error) {
        return {
          success: false,
          error: `Failed to read token metadata: ${getErrorMessage(error)}`,
        };
      }
''',
        "// Get token decimals and symbol via failover",
    ),
]:
    text = load(path)
    text = replace_once(text, '''    let receivedTransactionHash: string | undefined;
    try {
''', "", f"{path}: remove broad transaction catch start")
    text = replace_once(text, read_old, read_new, f"{path}: wrap pre-broadcast read")
    receipt_marker = '''      let receipt: Awaited<ReturnType<typeof adapter.executeContractCall>>;'''
    receipt_replacement = '''    let receivedTransactionHash: string | undefined;
    try {
      let receipt: Awaited<ReturnType<typeof adapter.executeContractCall>>;'''
    text = replace_next_after(text, anchor, receipt_marker, receipt_replacement, f"{path}: start send/confirm catch")
    save(path, text)

path = "app/api/execute/node/route.ts"
text = load(path)
text = replace_once(text, '''    // held. broadcastAttempted is still forwarded above so a hashless attempted
    // chain send becomes unconfirmed/reconcilable instead of terminal.
''', '''    // held. broadcastAttempted is still forwarded above so a hashless attempted
    // chain send fails closed as unconfirmed. The reconciler requires a hash,
    // so this shape is intentionally held rather than described as reconcilable.
''', "node: correct hashless reconciliation comment")
save(path, text)

path = "lib/tempo/held-payments.ts"
text = load(path)
text = replace_once(text, '''/** Rows sent to the node but not yet reconciled (poller path). A later tick
 *  checks each receipt and advances it to `confirmed` or `failed`. */
/** Move an unresolved broadcast to the back of the reconciliation queue.
''', '''/** Move an unresolved broadcast to the back of the reconciliation queue.
''', "held-payments: detach select doc comment")
text = replace_once(text, '''export async function selectBroadcastToReconcile(
  limit: number
): Promise<TempoHeldPayment[]> {
''', '''/** Rows sent to the node but not yet reconciled (poller path). A later tick
 *  checks each receipt and advances it to `confirmed` or `failed`. */
export async function selectBroadcastToReconcile(
  limit: number
): Promise<TempoHeldPayment[]> {
''', "held-payments: move select doc comment")
save(path, text)

path = "tests/unit/submit-signed.test.ts"
text = load(path)
text = replace_once(text, '''vi.mock("server-only", () => ({}));

import type { RpcOperationType, RpcProviderManager } from "@/lib/rpc/providers";
''', '''vi.mock("server-only", () => ({}));
vi.mock("@/lib/sleep", () => ({
  sleep: vi.fn().mockResolvedValue(undefined),
}));
vi.mock("@/lib/logging", () => ({
  ErrorCategory: { NETWORK_RPC: "network_rpc" },
  logSystemError: vi.fn(),
  logSystemWarn: vi.fn(),
}));

import { type RpcOperationType, RpcProviderManager } from "@/lib/rpc/providers";
''', "submit-signed test: provider imports/mocks")
anchor = '''describe("isNonceConflictError", () => {'''
insert = '''describe("RpcProviderManager write-broadcast retry evidence", () => {
  it("does not call an endpoint all-refused when an earlier retry timed out", async () => {
    const manager = new RpcProviderManager({
      config: {
        primaryRpcUrl: "http://127.0.0.1:1",
        maxRetries: 3,
        timeoutMs: 50,
        chainName: "retry-history-test",
        chainId: 1,
      },
    });
    let attempt = 0;

    const thrown = await manager
      .executeWithFailover(async () => {
        attempt += 1;
        if (attempt === 1) {
          throw new Error("Timeout after 50ms");
        }
        throw new Error("ECONNREFUSED");
      }, "write-broadcast")
      .then(
        () => undefined,
        (error: unknown) => error
      );

    expect(attempt).toBe(3);
    expect(thrown).toBeInstanceOf(Error);
    expect(
      (
        thrown as Error & {
          allAttemptsConnectionRefused?: boolean;
        }
      ).allAttemptsConnectionRefused
    ).toBe(false);
  });

  it("marks an endpoint all-refused only when every retry was refused", async () => {
    const manager = new RpcProviderManager({
      config: {
        primaryRpcUrl: "http://127.0.0.1:1",
        maxRetries: 3,
        timeoutMs: 50,
        chainName: "retry-history-test",
        chainId: 1,
      },
    });
    let attempt = 0;

    const thrown = await manager
      .executeWithFailover(async () => {
        attempt += 1;
        throw new Error("ECONNREFUSED");
      }, "write-broadcast")
      .then(
        () => undefined,
        (error: unknown) => error
      );

    expect(attempt).toBe(3);
    expect(
      (
        thrown as Error & {
          allAttemptsConnectionRefused?: boolean;
        }
      ).allAttemptsConnectionRefused
    ).toBe(true);
  });
});

'''
if anchor not in text:
    raise RuntimeError("submit-signed test: insertion anchor missing")
text = text.replace(anchor, insert + anchor, 1)
save(path, text)

path = "tests/unit/tempo-broadcast-stored.test.ts"
text = load(path)
text = replace_once(text, '''const mockSend = vi.fn();
const WINDOW_CLOSED_RE = /validity window closed/;
''', '''const mockSend = vi.fn();
const mockGetTransaction = vi.fn();
const WINDOW_CLOSED_RE = /validity window closed/;
''', "tempo test: add getTransaction mock")
text = replace_once(text, '''  mockGetRpcProvider.mockResolvedValue({
    executeWithFailover: (fn: (p: unknown) => unknown) =>
      Promise.resolve(fn({ send: (...a: unknown[]) => mockSend(...a) })),
    getProvider: () => ({}),
  });
''', '''  mockGetTransaction.mockResolvedValue(null);
  mockGetRpcProvider.mockResolvedValue({
    executeWithFailover: (fn: (p: unknown) => unknown) =>
      Promise.resolve(
        fn({
          send: (...a: unknown[]) => mockSend(...a),
          getTransaction: (...a: unknown[]) => mockGetTransaction(...a),
        })
      ),
    getProvider: () => ({}),
  });
''', "tempo test: provider supports hash lookup")
anchor = '''  it("rejects a non-Tempo chain before touching the blob", async () => {'''
insert = '''  it("keeps a send pending when earlier retry history was not all refusals", async () => {
    mockDeserialize.mockReturnValue({ validBefore: NOW_SEC + 100 });
    mockHash.mockReturnValue("0xhash");
    const masked = Object.assign(
      new Error(
        "RPC failed on both endpoints. Primary: ECONNREFUSED. Fallback: ECONNREFUSED"
      ),
      { allAttemptsConnectionRefused: false }
    );
    mockSend.mockRejectedValue(masked);

    const thrown = await broadcastStoredTempoTx({
      chainId: CHAIN,
      serialized: "0x76blob",
      waitForConfirmation: false,
    }).then(
      () => undefined,
      (error: unknown) => error
    );

    expect(isOnChainPendingError(thrown)).toBe(true);
    expect(
      (thrown as { transactionHash?: string }).transactionHash
    ).toBe("0xhash");
    expect(mockGetTransaction).not.toHaveBeenCalled();
  });

  it("treats an all-refused send as accepted when its deterministic hash is visible", async () => {
    mockDeserialize.mockReturnValue({ validBefore: NOW_SEC + 100 });
    mockHash.mockReturnValue("0xhash");
    const refused = Object.assign(new Error("ECONNREFUSED"), {
      allAttemptsConnectionRefused: true,
    });
    mockSend.mockRejectedValue(refused);
    mockGetTransaction.mockResolvedValue({ hash: "0xhash" });

    const result = await broadcastStoredTempoTx({
      chainId: CHAIN,
      serialized: "0x76blob",
      waitForConfirmation: false,
    });

    expect(result).toEqual({ hash: "0xhash", confirmed: false });
    expect(mockGetTransaction).toHaveBeenCalledWith("0xhash");
  });

  it("keeps an all-refused send terminal only after a readable hash miss", async () => {
    mockDeserialize.mockReturnValue({ validBefore: NOW_SEC + 100 });
    mockHash.mockReturnValue("0xhash");
    const refused = Object.assign(new Error("ECONNREFUSED"), {
      allAttemptsConnectionRefused: true,
    });
    mockSend.mockRejectedValue(refused);
    mockGetTransaction.mockResolvedValue(null);

    const thrown = await broadcastStoredTempoTx({
      chainId: CHAIN,
      serialized: "0x76blob",
      waitForConfirmation: false,
    }).then(
      () => undefined,
      (error: unknown) => error
    );

    expect(thrown).toBe(refused);
    expect(isOnChainPendingError(thrown)).toBe(false);
    expect(mockGetTransaction).toHaveBeenCalledWith("0xhash");
  });

'''
if anchor not in text:
    raise RuntimeError("tempo test: insertion anchor missing")
text = text.replace(anchor, insert + anchor, 1)
save(path, text)

print("review fixes applied")
