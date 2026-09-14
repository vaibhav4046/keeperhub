from pathlib import Path
import re


def load(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def save(path: str, text: str) -> None:
    Path(path).write_text(text, encoding="utf-8", newline="\n")


def replace_once(path: str, old: str, new: str) -> None:
    text = load(path)
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one exact match, got {count}: {old[:120]!r}")
    save(path, text.replace(old, new, 1))


def regex_once(path: str, pattern: str, repl: str, flags: int = 0) -> None:
    text = load(path)
    new, count = re.subn(pattern, repl, text, count=1, flags=flags)
    if count != 1:
        raise RuntimeError(f"{path}: expected one regex match, got {count}: {pattern}")
    save(path, new)


def add_failure_field(path: str, anchor: str = "      sponsored?: boolean;\n") -> None:
    text = load(path)
    if "      broadcastAttempted?: boolean;\n" in text:
        return
    if text.count(anchor) != 1:
        raise RuntimeError(f"{path}: cannot place broadcastAttempted; anchor count={text.count(anchor)}")
    save(path, text.replace(anchor, anchor + "      broadcastAttempted?: boolean;\n", 1))


def wrap_core(path: str, fn: str, input_type: str, result_type: str) -> None:
    text = load(path)
    exported = f"export async function {fn}("
    internal = f"async function {fn}Impl("
    if internal in text:
        raise RuntimeError(f"{path}: wrapper already applied")
    if text.count(exported) != 1:
        raise RuntimeError(f"{path}: expected one {exported!r}, got {text.count(exported)}")
    text = text.replace(exported, internal, 1)
    wrapper = f'''\n\n/**\n * Normalize send-boundary evidence for idempotency. All direct EVM paths in\n * this core use the signed-send adapter/Safe helpers: once submission is\n * attempted, an uncertain outcome retains its deterministic transaction hash.\n * Therefore a hashless non-sponsored failure from this core is provably\n * pre-broadcast. Sponsored terminal failures are explicitly attempted.\n */\nexport async function {fn}(\n  input: {input_type}\n): Promise<{result_type}> {{\n  const result = await {fn}Impl(input);\n  if (result.success || result.broadcastAttempted !== undefined) {{\n    return result;\n  }}\n  return {{\n    ...result,\n    broadcastAttempted:\n      result.sponsored === true || Boolean(result.transactionHash),\n  }};\n}}\n'''
    save(path, text.rstrip() + wrapper)


# EVM cores: make the export boundary total over broadcast evidence. The
# deterministic-hash invariant is enforced below the cores by submit-signed.ts.
add_failure_field("plugins/web3/steps/approve-token-core.ts")
wrap_core(
    "plugins/web3/steps/approve-token-core.ts",
    "approveTokenCore",
    "ApproveTokenCoreInput",
    "ApproveTokenResult",
)

# These three already expose the optional field in their failure variants.
wrap_core(
    "plugins/web3/steps/write-contract-core.ts",
    "writeContractCore",
    "WriteContractCoreInput",
    "WriteContractResult",
)
wrap_core(
    "plugins/web3/steps/transfer-token-core.ts",
    "transferTokenCore",
    "TransferTokenCoreInput",
    "TransferTokenResult",
)
wrap_core(
    "plugins/web3/steps/transfer-funds-core.ts",
    "transferFundsCore",
    "TransferFundsCoreInput",
    "TransferFundsResult",
)

# Batch and Robinhood use the same EVM signed-send boundary but did not expose
# the evidence field yet.
add_failure_field(
    "plugins/web3/steps/batch-write-contract-core.ts",
    anchor="      chainId?: number;\n",
)
wrap_core(
    "plugins/web3/steps/batch-write-contract-core.ts",
    "batchWriteContractCore",
    "BatchWriteContractCoreInput",
    "BatchWriteContractResult",
)
add_failure_field(
    "plugins/robinhood/steps/trade-stock-token-core.ts",
    anchor="      chainId?: number;\n",
)
wrap_core(
    "plugins/robinhood/steps/trade-stock-token-core.ts",
    "tradeStockTokenCore",
    "TradeStockTokenCoreInput",
    "TradeStockTokenResult",
)

# Solana: preserve deterministic signature/hash through every post-submit
# failure, so the transfer cores can distinguish never-sent from may-have-sent.
solana = "lib/web3/chain-adapter/solana.ts"
replace_once(
    solana,
    'import { getErrorMessage } from "@/lib/utils";\n',
    'import { getErrorMessage } from "@/lib/utils";\nimport {\n  OnChainPendingError,\n  OnChainRevertError,\n} from "@/lib/web3/onchain-revert";\n',
)
replace_once(
    solana,
    '''    throw new Error(\n      `[SolanaChainAdapter] Transaction ${signature} did not confirm and its blockhash has not expired; refusing to re-sign while it may still land`\n    );''',
    '''    throw new OnChainPendingError({\n      message: `[SolanaChainAdapter] Transaction ${signature} did not confirm and its blockhash has not expired; refusing to re-sign while it may still land`,\n      transactionHash: signature,\n    });''',
)
old_confirmation = '''    const { confirmationErr, txResult } = await this.executeWithSolanaFailover(\n      async (connection) => {\n        const confirmation = await connection.confirmTransaction(\n          {\n            signature,\n            blockhash: blockhashRefs.blockhash,\n            lastValidBlockHeight: blockhashRefs.lastValidBlockHeight,\n          },\n          "confirmed"\n        );\n\n        const tx = await connection.getTransaction(signature, {\n          commitment: "confirmed",\n          maxSupportedTransactionVersion: 0,\n        });\n        return { confirmationErr: confirmation.value.err, txResult: tx };\n      },\n      "read"\n    );\n\n    if (confirmationErr) {\n      throw new Error(\n        `[SolanaChainAdapter] Transaction ${signature} failed on-chain: ${JSON.stringify(confirmationErr)}`\n      );\n    }\n    if (txResult?.meta?.err) {\n      throw new Error(\n        `[SolanaChainAdapter] Transaction ${signature} reverted on-chain: ${JSON.stringify(txResult.meta.err)}`\n      );\n    }'''
new_confirmation = '''    let confirmationState: Awaited<\n      ReturnType<SolanaChainAdapter["readConfirmationState"]>\n    >;\n    try {\n      confirmationState = await this.readConfirmationState(\n        signature,\n        blockhashRefs\n      );\n    } catch (error) {\n      if (error instanceof OnChainRevertError || error instanceof OnChainPendingError) {\n        throw error;\n      }\n      throw new OnChainPendingError({\n        message: `[SolanaChainAdapter] Transaction ${signature} was submitted but confirmation could not be read: ${getErrorMessage(error)}`,\n        transactionHash: signature,\n      });\n    }\n    const { confirmationErr, txResult } = confirmationState;\n\n    if (confirmationErr) {\n      throw new OnChainRevertError({\n        message: `[SolanaChainAdapter] Transaction ${signature} failed on-chain: ${JSON.stringify(confirmationErr)}`,\n        transactionHash: signature,\n      });\n    }\n    if (txResult?.meta?.err) {\n      throw new OnChainRevertError({\n        message: `[SolanaChainAdapter] Transaction ${signature} reverted on-chain: ${JSON.stringify(txResult.meta.err)}`,\n        transactionHash: signature,\n        blockNumber: txResult.slot,\n      });\n    }'''
# Rather than duplicate the long RPC read inside the try block, add a private
# helper immediately before sendTransaction and replace the existing block.
helper_anchor = '''  async sendTransaction(\n    _signer: ethers.Signer, // Unused: Solana uses options.solanaSigner\n'''
helper = '''  private async readConfirmationState(\n    signature: string,\n    blockhashRefs: SolanaBlockhashRefs\n  ) {\n    return this.executeWithSolanaFailover(\n      async (connection) => {\n        const confirmation = await connection.confirmTransaction(\n          {\n            signature,\n            blockhash: blockhashRefs.blockhash,\n            lastValidBlockHeight: blockhashRefs.lastValidBlockHeight,\n          },\n          "confirmed"\n        );\n\n        const tx = await connection.getTransaction(signature, {\n          commitment: "confirmed",\n          maxSupportedTransactionVersion: 0,\n        });\n        return { confirmationErr: confirmation.value.err, txResult: tx };\n      },\n      "read"\n    );\n  }\n\n  async sendTransaction(\n    _signer: ethers.Signer, // Unused: Solana uses options.solanaSigner\n'''
replace_once(solana, helper_anchor, helper)
replace_once(solana, old_confirmation, new_confirmation)

# A blockhash-expiry error occurs after a send attempt. If fate-checking itself
# fails, retain the deterministic signature rather than leaking a hashless error.
old_settle_call = '''          const settled = await this.settlePriorAttempt(\n            signedAttempt,\n            blockhashRefs\n          );'''
new_settle_call = '''          let settled: string | null;\n          try {\n            settled = await this.settlePriorAttempt(signedAttempt, blockhashRefs);\n          } catch (settleError) {\n            const priorSignature = deriveSolanaSignature(signedAttempt.signedBytes);\n            if (!priorSignature) {\n              throw settleError;\n            }\n            throw new OnChainPendingError({\n              message: `[SolanaChainAdapter] Prior broadcast ${priorSignature} could not be reconciled: ${getErrorMessage(settleError)}`,\n              transactionHash: priorSignature,\n            });\n          }'''
replace_once(solana, old_settle_call, new_settle_call)

# Solana token core: every error caught around adapter.sendTransaction is now
# either hash-bearing post-submit or hashless pre-submit.
spl = "plugins/web3/steps/transfer-spl-token-core.ts"
replace_once(
    spl,
    'import { getErrorMessage } from "@/lib/utils";\n',
    'import { getErrorMessage } from "@/lib/utils";\nimport { broadcastTransactionHash } from "@/lib/web3/onchain-revert";\n',
)
replace_once(
    spl,
    '  | { success: false; error: string };',
    '''  | {\n      success: false;\n      error: string;\n      transactionHash?: string;\n      chainId?: number;\n      broadcastAttempted?: boolean;\n    };''',
)
old_spl_catch = '''    return { success: false, error: getErrorMessage(error) };\n  }\n}\n\nexport async function transferSplTokenCore'''
new_spl_catch = '''    const transactionHash = broadcastTransactionHash(error);\n    return {\n      success: false,\n      error: getErrorMessage(error),\n      broadcastAttempted: Boolean(transactionHash),\n      ...(transactionHash ? { transactionHash, chainId } : {}),\n    };\n  }\n}\n\nexport async function transferSplTokenCore'''
replace_once(spl, old_spl_catch, new_spl_catch)

# Native Solana transfer catches the same adapter errors. Preserve the hash and
# explicit boundary evidence instead of relying on a route special case.
funds = "plugins/web3/steps/transfer-funds-core.ts"
old_funds_solana = '''    return {\n      success: false,\n      error: getErrorMessage(error),\n    };\n  }\n}'''
new_funds_solana = '''    const transactionHash = broadcastTransactionHash(error);\n    return {\n      success: false,\n      error: getErrorMessage(error),\n      broadcastAttempted: Boolean(transactionHash),\n      ...(transactionHash ? { transactionHash, chainId } : {}),\n    };\n  }\n}'''
# This exact tail is unique to transferFundsSolana.
if load(funds).count(old_funds_solana) != 1:
    raise RuntimeError("transfer-funds-core.ts: expected unique Solana catch tail")
replace_once(funds, old_funds_solana, new_funds_solana)

# With Solana reporting evidence, the route no longer needs a family-specific
# fail-closed branch. The common classifier is now the single source of truth.
route = "app/api/execute/transfer/route.ts"
replace_once(
    route,
    '''  const disposition =\n    isSolanaTransfer && !result.transactionHash\n      ? "failed"\n      : dispositionForExecutionOutcome(outcome.status, result);''',
    '''  const disposition = dispositionForExecutionOutcome(outcome.status, result);''',
)

# Tempo already computes the deterministic tx hash before broadcast. Keep it on
# ambiguous send/receipt errors so callers can classify them exactly like EVM.
tempo = "plugins/tempo/steps/tempo-tx-core.ts"
replace_once(
    tempo,
    'import { getOrganizationWallet } from "@/lib/web3/wallet-helpers";\n',
    'import { getOrganizationWallet } from "@/lib/web3/wallet-helpers";\nimport { OnChainPendingError, OnChainRevertError } from "@/lib/web3/onchain-revert";\n',
)
replace_once(
    tempo,
    '  throw new Error(`Timed out waiting for Tempo transaction receipt (${hash})`);',
    '''  throw new OnChainPendingError({\n    message: `Timed out waiting for Tempo transaction receipt (${hash})`,\n    transactionHash: hash,\n  });''',
)
replace_once(
    tempo,
    '''  if (expectedHash) {\n    const actual = TxEnvelopeTempo.hash(envelope as TxEnvelopeTempo.Signed);\n    if (actual.toLowerCase() !== expectedHash.toLowerCase()) {''',
    '''  const deterministicHash = TxEnvelopeTempo.hash(\n    envelope as TxEnvelopeTempo.Signed\n  );\n  if (expectedHash) {\n    if (deterministicHash.toLowerCase() !== expectedHash.toLowerCase()) {''',
)
old_tempo_throw = '''    throw error;\n  }\n\n  if (!waitForConfirmation) {'''
new_tempo_throw = '''    if (isFundingShortfall(message)) {\n      // Node rejected the transaction before accepting it; safe to report as\n      // a definite pre-broadcast failure.\n      throw error;\n    }\n    throw new OnChainPendingError({\n      message: `Tempo transaction ${deterministicHash} may have been broadcast: ${message}`,\n      transactionHash: deterministicHash,\n    });\n  }\n\n  if (!waitForConfirmation) {'''
replace_once(tempo, old_tempo_throw, new_tempo_throw)
replace_once(
    tempo,
    '    throw new Error(`Tempo transaction reverted (${hash})`);',
    '''    throw new OnChainRevertError({\n      message: `Tempo transaction reverted (${hash})`,\n      transactionHash: hash,\n      blockNumber: receipt.blockNumber,\n    });''',
)

# Tempo step catches: expose the structured deterministic hash and boundary flag
# to the generic direct-execution result contract.
for path, result_type in [
    ("plugins/tempo/steps/dex-swap.ts", "DexSwapResult"),
    ("plugins/tempo/steps/transfer-with-memo.ts", "TransferWithMemoResult"),
    ("plugins/tempo/steps/batch-payout.ts", "BatchPayoutResult"),
]:
    text = load(path)
    if 'broadcastTransactionHash' not in text:
        marker = 'import { getErrorMessage } from "@/lib/utils";\n'
        if marker not in text:
            raise RuntimeError(f"{path}: getErrorMessage import marker missing")
        text = text.replace(
            marker,
            marker + 'import { broadcastTransactionHash } from "@/lib/web3/onchain-revert";\n',
            1,
        )
    # Expand the simple failure arm used by all three result unions.
    simple = '  | { success: false; error: string };'
    if simple in text:
        text = text.replace(
            simple,
            '''  | {\n      success: false;\n      error: string;\n      transactionHash?: string;\n      chainId?: number;\n      broadcastAttempted?: boolean;\n    };''',
            1,
        )
    else:
        raise RuntimeError(f"{path}: simple failure result arm missing")
    # Replace only the final catch return in each step. All pre-send errors in
    # the same try are hashless; transport ambiguity is structured by tempo core.
    old = '    return { success: false, error: getErrorMessage(error) };\n  }\n}'
    if text.count(old) != 1:
        raise RuntimeError(f"{path}: expected one final failure return, got {text.count(old)}")
    new = '''    const transactionHash = broadcastTransactionHash(error);\n    return {\n      success: false,\n      error: getErrorMessage(error),\n      broadcastAttempted: Boolean(transactionHash),\n      ...(transactionHash ? { transactionHash, chainId } : {}),\n    };\n  }\n}'''
    text = text.replace(old, new, 1)
    save(path, text)

# Guardrail: the five cores called out by review plus the original EVM cores all
# contain explicit evidence at their exported boundary after this patch.
for path in [
    "plugins/web3/steps/approve-token-core.ts",
    "plugins/web3/steps/batch-write-contract-core.ts",
    "plugins/web3/steps/write-contract-core.ts",
    "plugins/web3/steps/transfer-token-core.ts",
    "plugins/web3/steps/transfer-funds-core.ts",
    "plugins/web3/steps/transfer-spl-token-core.ts",
    "plugins/robinhood/steps/trade-stock-token-core.ts",
]:
    if "broadcastAttempted" not in load(path):
        raise RuntimeError(f"{path}: broadcast evidence missing after patch")

print("issue1840 reviewer patch applied successfully")
