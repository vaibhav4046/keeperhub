import "server-only";
import {
  type SignatureStatus,
  Transaction,
  VersionedTransaction,
} from "@solana/web3.js";
import bs58 from "bs58";
import type { SolanaProviderManager } from "@/lib/rpc/providers/solana";
import { sleep } from "@/lib/sleep";
import { OnChainPendingError } from "@/lib/web3/onchain-revert";

function extractFirstSignature(signedBytes: Uint8Array): Uint8Array | null {
  try {
    const sig = VersionedTransaction.deserialize(signedBytes).signatures[0];
    return sig ?? null;
  } catch {
    try {
      const legacySig = Transaction.from(signedBytes).signatures[0]?.signature;
      if (!legacySig) {
        return null;
      }
      return legacySig instanceof Buffer
        ? new Uint8Array(legacySig)
        : legacySig;
    } catch {
      return null;
    }
  }
}

/**
 * The base58 signature a set of signed bytes will carry on chain, or null when
 * the bytes cannot be parsed. Deterministic: the signature is fixed at signing,
 * so it identifies the transaction before it is ever broadcast.
 */
export function deriveSolanaSignature(signedBytes: Uint8Array): string | null {
  const firstSig = extractFirstSignature(signedBytes);
  return firstSig ? bs58.encode(firstSig) : null;
}

/**
 * Attempts before giving up on a signature that has not surfaced yet. A
 * transaction the RPC accepted needs a slot or two to become queryable, so a
 * single immediate lookup cannot tell "still propagating" from "never landed".
 */
export const RECONCILE_ATTEMPTS = 5;
export const RECONCILE_DELAY_MS = 1500;

/** Overrides for the reconcile poll. Exists so tests need not sleep. */
export type ReconcileOptions = {
  attempts?: number;
  delayMs?: number;
};

/**
 * Polls for the deterministic signature after a send error. Any observed
 * status is enough to prove that the signed transaction reached the network;
 * the adapter performs the authoritative confirmation/revert read afterwards.
 * A missing status remains unknown until all attempts are exhausted.
 */
async function hasSignatureSurfaced(
  signature: string,
  manager: SolanaProviderManager,
  options: ReconcileOptions
): Promise<boolean> {
  const attempts = options.attempts ?? RECONCILE_ATTEMPTS;
  const delayMs = options.delayMs ?? RECONCILE_DELAY_MS;

  for (let attempt = 0; attempt < attempts; attempt++) {
    if (attempt > 0) {
      await sleep(delayMs);
    }

    let statusResult: SignatureStatus | null = null;
    try {
      statusResult = await manager.executeWithFailover(async (connection) => {
        const res = await connection.getSignatureStatuses([signature]);
        return res.value[0];
      }, "read");
    } catch {
      // A read that fails on every endpoint says nothing about the
      // transaction; keep polling rather than concluding it never landed.
      continue;
    }

    if (statusResult) {
      return true;
    }
  }

  return false;
}

export async function submitSignedSolanaTransactionWithFailover(
  signedBytes: Uint8Array,
  manager: SolanaProviderManager,
  reconcileOptions: ReconcileOptions = {}
): Promise<{ signature: string }> {
  try {
    // sendRawTransaction returns the transaction signature as a base58 string —
    // no manual encoding needed on the success path.
    const signature = await manager.executeWithFailover(
      (connection) =>
        connection.sendRawTransaction(signedBytes, {
          skipPreflight: true,
          maxRetries: 0,
        }),
      "write-broadcast"
    );
    return { signature };
  } catch (err) {
    // The signed bytes already determine the transaction signature before the
    // network call. Preserve that identity across a lost/ambiguous send reply
    // exactly as the EVM signed-send helper preserves its deterministic hash.
    // If the signature has surfaced, return it and let SolanaChainAdapter read
    // the authoritative confirmation/revert state. If it has not surfaced,
    // the send outcome is still unknown: throw a structured pending error with
    // the signature rather than collapsing it into a hashless failure that an
    // idempotent retry could mistake for "never broadcast".
    const signature = deriveSolanaSignature(signedBytes);
    if (!signature) {
      throw err;
    }

    if (await hasSignatureSurfaced(signature, manager, reconcileOptions)) {
      return { signature };
    }

    throw new OnChainPendingError({
      message:
        err instanceof Error
          ? `Solana transaction send outcome could not be determined (${err.message})`
          : `Solana transaction send outcome could not be determined (${String(err)})`,
      transactionHash: signature,
    });
  }
}
