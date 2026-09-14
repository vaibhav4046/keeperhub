from pathlib import Path
import re

changed = []


def read(path: str) -> str:
    return Path(path).read_text()


def write(path: str, text: str) -> None:
    Path(path).write_text(text)
    if path not in changed:
        changed.append(path)


def exact(path: str, old: str, new: str, count: int = 1) -> None:
    text = read(path)
    found = text.count(old)
    if found != count:
        raise SystemExit(f"{path}: expected {count} exact matches, found {found}: {old[:120]!r}")
    write(path, text.replace(old, new, count))


def regex1(path: str, pattern: str, repl: str, flags: int = 0) -> None:
    text = read(path)
    new, n = re.subn(pattern, repl, text, count=1, flags=flags)
    if n != 1:
        raise SystemExit(f"{path}: regex match count {n}: {pattern[:120]!r}")
    write(path, new)


def add_import(path: str, statement: str) -> None:
    text = read(path)
    if statement in text:
        return
    marker = 'import "server-only";\n'
    if marker not in text:
        raise SystemExit(f"{path}: missing server-only import marker")
    write(path, text.replace(marker, marker + statement + "\n", 1))


def add_failure_field(path: str, type_name: str, field: str) -> None:
    text = read(path)
    start = text.find(f"export type {type_name} =")
    if start < 0:
        raise SystemExit(f"{path}: result type {type_name} not found")
    fail = text.find("success: false;", start)
    if fail < 0:
        raise SystemExit(f"{path}: failure variant not found")
    end = text.find("};", fail)
    if end < 0:
        raise SystemExit(f"{path}: failure variant end not found")
    segment = text[fail:end]
    if field.strip() in segment:
        return
    indent = "      " if "      success: false;" in text[start:end] else "    "
    text = text[:end] + indent + field.strip() + "\n    " + text[end:]
    write(path, text)


def wrap_export(path: str, fn: str, input_type: str, result_type: str) -> None:
    text = read(path)
    needle = f"export async function {fn}("
    if text.count(needle) != 1:
        raise SystemExit(f"{path}: expected one exported function {fn}")
    text = text.replace(needle, f"async function {fn}Impl(", 1)
    wrapper = f'''\n\nexport async function {fn}(\n  input: {input_type}\n): Promise<{result_type}> {{\n  const result = await {fn}Impl(input);\n  if (result.success || result.broadcastAttempted !== undefined) {{\n    return result;\n  }}\n  return {{ ...result, broadcastAttempted: false }};\n}}\n'''
    write(path, text.rstrip() + wrapper)


# 1. Fail closed by default. A terminal hash-bearing failure is reconciled and
# safe to release; missing evidence is never enough to permit a retry.
regex1(
    "lib/idempotency-disposition.ts",
    r'''  if \(status === "completed"\) \{\n    return "success";\n  \}\n  if \(status === "unconfirmed"\) \{\n    return "failed";\n  \}\n  if \(\n    !evidence\?\.transactionHash &&\n    \(evidence\?\.broadcastAttempted === true \|\| evidence\?\.sponsored === true\)\n  \) \{\n    return "failed";\n  \}\n  return "release";''',
    '''  if (status === "completed") {\n    return "success";\n  }\n  if (status === "unconfirmed") {\n    return "failed";\n  }\n  if (evidence?.transactionHash) {\n    return "release";\n  }\n  if (evidence?.sponsored === true) {\n    return "failed";\n  }\n  if (evidence?.broadcastAttempted === false) {\n    return "release";\n  }\n  return "failed";''',
)

# Reuse the same all-endpoints-refused predicate that the signed EVM sender uses.
exact(
    "lib/web3/submit-signed.ts",
    "function isDefinitelyPreBroadcastNetworkError(error: unknown): boolean {",
    "export function isDefinitelyPreBroadcastNetworkError(error: unknown): boolean {",
)

write(
    "tests/unit/idempotency-disposition-evidence.test.ts",
    '''import { describe, expect, it } from "vitest";\nimport { dispositionForExecutionOutcome } from "@/lib/idempotency-disposition";\n\ndescribe("idempotency disposition execution evidence", () => {\n  it("fails closed when a failed execution has no broadcast evidence", () => {\n    expect(dispositionForExecutionOutcome("failed")).toBe("failed");\n  });\n\n  it("releases only explicit pre-broadcast failures", () => {\n    expect(\n      dispositionForExecutionOutcome("failed", { broadcastAttempted: false })\n    ).toBe("release");\n  });\n\n  it("holds hashless attempted sends", () => {\n    expect(\n      dispositionForExecutionOutcome("failed", { broadcastAttempted: true })\n    ).toBe("failed");\n  });\n\n  it("holds hashless sponsored sends", () => {\n    expect(dispositionForExecutionOutcome("failed", { sponsored: true })).toBe(\n      "failed"\n    );\n  });\n\n  it("releases a terminal reconciled failure carrying a transaction hash", () => {\n    expect(\n      dispositionForExecutionOutcome("failed", {\n        transactionHash: `0x${"11".repeat(32)}`,\n        broadcastAttempted: true,\n      })\n    ).toBe("release");\n  });\n\n  it("always holds unconfirmed outcomes", () => {\n    expect(\n      dispositionForExecutionOutcome("unconfirmed", { broadcastAttempted: false })\n    ).toBe("failed");\n  });\n});\n''',
)

network_import = 'import { isDefinitelyPreBroadcastNetworkError } from "@/lib/web3/submit-signed";'

# 2. Direct EVM broadcaster cores. Early returns are pre-broadcast and the
# exported wrapper marks them false. The send catch marks known static-call
# rejection / all-endpoint refusal false and everything ambiguous true.
for path, fn, input_t, result_t, type_t in [
    ("plugins/web3/steps/transfer-token-core.ts", "transferTokenCore", "TransferTokenCoreInput", "TransferTokenResult", "TransferTokenResult"),
    ("plugins/web3/steps/write-contract-core.ts", "writeContractCore", "WriteContractCoreInput", "WriteContractResult", "WriteContractResult"),
    ("plugins/web3/steps/approve-token-core.ts", "approveTokenCore", "ApproveTokenCoreInput", "ApproveTokenResult", "ApproveTokenResult"),
]:
    add_import(path, network_import)
    add_failure_field(path, type_t, "broadcastAttempted?: boolean;")
    text = read(path)
    marker = '''        ...(broadcastTransactionHash(error)\n          ? { transactionHash: broadcastTransactionHash(error), chainId }\n          : {}),'''
    if text.count(marker) != 1:
        raise SystemExit(f"{path}: direct catch transaction-hash marker count={text.count(marker)}")
    evidence = '''        broadcastAttempted:\n          broadcastTransactionHash(error)\n            ? true\n            : rejection.kind !== "unknown" ||\n                isDefinitelyPreBroadcastNetworkError(error)\n              ? false\n              : true,\n'''
    write(path, text.replace(marker, evidence + marker, 1))
    wrap_export(path, fn, input_t, result_t)

# Approve sponsored path was the only sponsored write not forwarding the
# resolver's explicit attempted-send evidence.
exact(
    "plugins/web3/steps/approve-token-core.ts",
    '''          sponsored: true,\n          ...(decision.transactionHash''',
    '''          sponsored: true,\n          broadcastAttempted: decision.broadcastAttempted,\n          ...(decision.transactionHash''',
)

# Native transfer includes an EVM and a Solana branch.
path = "plugins/web3/steps/transfer-funds-core.ts"
add_import(path, network_import)
add_failure_field(path, "TransferFundsResult", "broadcastAttempted?: boolean;")
text = read(path)
marker = '''        ...(broadcastTransactionHash(error)\n          ? { transactionHash: broadcastTransactionHash(error), chainId }\n          : {}),'''
if text.count(marker) != 1:
    raise SystemExit(f"{path}: EVM catch marker count={text.count(marker)}")
evidence = '''        broadcastAttempted:\n          broadcastTransactionHash(error)\n            ? true\n            : rejection.kind !== "unknown" ||\n                isDefinitelyPreBroadcastNetworkError(error)\n              ? false\n              : true,\n'''
text = text.replace(marker, evidence + marker, 1)
sol_old = '''    return {\n      success: false,\n      error: getErrorMessage(error),\n    };\n  }\n}'''
sol_new = '''    return {\n      success: false,\n      error: getErrorMessage(error),\n      broadcastAttempted: true,\n    };\n  }\n}'''
if text.count(sol_old) != 1:
    raise SystemExit(f"{path}: Solana send catch count={text.count(sol_old)}")
write(path, text.replace(sol_old, sol_new, 1))
wrap_export(path, "transferFundsCore", "TransferFundsCoreInput", "TransferFundsResult")

# Batch EVM write.
path = "plugins/web3/steps/batch-write-contract-core.ts"
add_import(path, network_import)
add_failure_field(path, "BatchWriteContractResult", "broadcastAttempted?: boolean;")
text = read(path)
old = '''        ...(broadcastHash ? { transactionHash: broadcastHash, chainId } : {}),\n        ...(rejection.kind !== "unknown" ? { rejection } : {}),'''
new = '''        ...(broadcastHash ? { transactionHash: broadcastHash, chainId } : {}),\n        broadcastAttempted: broadcastHash\n          ? true\n          : rejection.kind !== "unknown" ||\n              isDefinitelyPreBroadcastNetworkError(error)\n            ? false\n            : true,\n        ...(rejection.kind !== "unknown" ? { rejection } : {}),'''
if text.count(old) != 1:
    raise SystemExit(f"{path}: catch base marker count={text.count(old)}")
write(path, text.replace(old, new, 1))
wrap_export(path, "batchWriteContractCore", "BatchWriteContractCoreInput", "BatchWriteContractResult")

# Robinhood router write. Its try block is the actual nonce/send section, so a
# hashless exception there is held unless it is a decoded revert or every RPC
# endpoint definitely refused the request.
path = "plugins/robinhood/steps/trade-stock-token-core.ts"
add_import(path, network_import)
add_import(path, 'import { classifyRevert } from "@/lib/web3/decode-revert-error";')
add_failure_field(path, "TradeStockTokenResult", "broadcastAttempted?: boolean;")
old = '''    return {\n      success: false,\n      error: getErrorMessage(error),\n      ...(broadcastTransactionHash(error)\n        ? { transactionHash: broadcastTransactionHash(error), chainId }\n        : {}),\n    };'''
new = '''    const broadcastHash = broadcastTransactionHash(error);\n    const rejection = classifyRevert(\n      error,\n      new ethers.Interface(UNIVERSAL_ROUTER_ABI)\n    );\n    return {\n      success: false,\n      error: getErrorMessage(error),\n      broadcastAttempted: broadcastHash\n        ? true\n        : rejection.kind !== "unknown" ||\n            isDefinitelyPreBroadcastNetworkError(error)\n          ? false\n          : true,\n      ...(broadcastHash ? { transactionHash: broadcastHash, chainId } : {}),\n    };'''
exact(path, old, new)
wrap_export(path, "tradeStockTokenCore", "TradeStockTokenCoreInput", "TradeStockTokenResult")

# SPL transfer: all validation/preflight paths remain explicit false via wrapper;
# once adapter.sendTransaction is entered, a missing signature is ambiguous.
path = "plugins/web3/steps/transfer-spl-token-core.ts"
add_failure_field(path, "TransferSplTokenResult", "broadcastAttempted?: boolean;")
exact(
    path,
    '''    return { success: false, error: getErrorMessage(error) };\n  }\n}\n\nexport async function transferSplTokenCore(''',
    '''    return {\n      success: false,\n      error: getErrorMessage(error),\n      broadcastAttempted: true,\n    };\n  }\n}\n\nexport async function transferSplTokenCore(''',
)
wrap_export(path, "transferSplTokenCore", "TransferSplTokenCoreInput", "TransferSplTokenResult")

# 3. Tempo computes a deterministic signed hash before raw submission. Preserve
# it through lost send replies, confirmation read failures, and mined reverts.
path = "plugins/tempo/steps/tempo-tx-core.ts"
add_import(path, 'import { OnChainPendingError, OnChainRevertError } from "@/lib/web3/onchain-revert";')
add_import(path, network_import)
exact(
    path,
    '''  if (expectedHash) {\n    const actual = TxEnvelopeTempo.hash(envelope as TxEnvelopeTempo.Signed);\n    if (actual.toLowerCase() !== expectedHash.toLowerCase()) {''',
    '''  const actualHash = TxEnvelopeTempo.hash(envelope as TxEnvelopeTempo.Signed);\n  if (expectedHash) {\n    if (actualHash.toLowerCase() !== expectedHash.toLowerCase()) {''',
)
exact(
    path,
    '''    throw error;\n  }\n\n  if (!waitForConfirmation) {\n    return { hash, confirmed: false };\n  }\n\n  const receipt = await waitForReceipt(rpcManager, hash);\n  if (receipt.status === 0) {\n    throw new Error(`Tempo transaction reverted (${hash})`);\n  }''',
    '''    if (isDefinitelyPreBroadcastNetworkError(error)) {\n      throw error;\n    }\n    throw new OnChainPendingError({\n      message: `Tempo transaction send outcome could not be determined (${message})`,\n      transactionHash: actualHash,\n    });\n  }\n\n  if (!waitForConfirmation) {\n    return { hash, confirmed: false };\n  }\n\n  let receipt: ethers.TransactionReceipt;\n  try {\n    receipt = await waitForReceipt(rpcManager, hash);\n  } catch (error) {\n    throw new OnChainPendingError({\n      message: error instanceof Error ? error.message : String(error),\n      transactionHash: actualHash,\n    });\n  }\n  if (receipt.status === 0) {\n    throw new OnChainRevertError({\n      message: `Tempo transaction reverted (${hash})`,\n      transactionHash: actualHash,\n      blockNumber: receipt.blockNumber,\n    });\n  }''',
)
exact(
    path,
    '''    serialized: signed.serialized,\n    waitForConfirmation: true,''',
    '''    serialized: signed.serialized,\n    expectedHash: signed.hash,\n    waitForConfirmation: true,''',
)

# Tempo route-facing actions propagate the hash-bearing error from tempo-tx-core
# and retain the hash across explorer-link decoration failures.
for path, input_t, result_t in [
    ("plugins/tempo/steps/transfer-with-memo.ts", "TransferWithMemoInput", "TransferWithMemoResult"),
    ("plugins/tempo/steps/dex-swap.ts", "DexSwapInput", "DexSwapResult"),
    ("plugins/tempo/steps/batch-payout.ts", "BatchPayoutInput", "BatchPayoutResult"),
]:
    add_import(path, 'import { broadcastTransactionHash } from "@/lib/web3/onchain-revert";')
    add_failure_field(path, result_t, "transactionHash?: string;\n      chainId?: number;\n      broadcastAttempted?: boolean;")
    text = read(path)
    decl = f"async function stepHandler(input: {input_t}): Promise<{result_t}> {{"
    if text.count(decl) != 1:
        raise SystemExit(f"{path}: stepHandler declaration count={text.count(decl)}")
    text = text.replace(decl, f"async function stepHandlerImpl(input: {input_t}): Promise<{result_t}> {{", 1)
    try_marker = '''  try {\n    const rpcManager = await getRpcProvider({'''
    if text.count(try_marker) != 1:
        raise SystemExit(f"{path}: main try marker count={text.count(try_marker)}")
    text = text.replace(try_marker, '''  let broadcastHash: string | undefined;\n\n  try {\n    const rpcManager = await getRpcProvider({''', 1)
    post = '''\n\n    const transactionLink = await buildTempoTxLink(chainId, hash);'''
    if text.count(post) != 1:
        raise SystemExit(f"{path}: transactionLink marker count={text.count(post)}")
    text = text.replace(post, '''\n    broadcastHash = hash;\n\n    const transactionLink = await buildTempoTxLink(chainId, hash);''', 1)
    catch = '''    return { success: false, error: getErrorMessage(error) };'''
    if text.count(catch) != 1:
        raise SystemExit(f"{path}: terminal catch marker count={text.count(catch)}")
    catch_new = '''    const transactionHash = broadcastHash ?? broadcastTransactionHash(error);\n    return {\n      success: false,\n      error: getErrorMessage(error),\n      broadcastAttempted: transactionHash ? true : false,\n      ...(transactionHash ? { transactionHash, chainId } : {}),\n    };'''
    text = text.replace(catch, catch_new, 1)
    export_pos = text.find("\nexport async function ")
    if export_pos < 0:
        raise SystemExit(f"{path}: exported workflow step marker missing")
    wrapper = f'''\nasync function stepHandler(input: {input_t}): Promise<{result_t}> {{\n  const result = await stepHandlerImpl(input);\n  if (result.success || result.broadcastAttempted !== undefined) {{\n    return result;\n  }}\n  return {{ ...result, broadcastAttempted: false }};\n}}\n'''
    text = text[:export_pos] + wrapper + text[export_pos:]
    write(path, text)

# Shared evidence now covers Solana; remove the route-specific fail-closed branch.
exact(
    "app/api/execute/transfer/route.ts",
    '''  const disposition =\n    isSolanaTransfer && !result.transactionHash\n      ? "failed"\n      : dispositionForExecutionOutcome(outcome.status, result);''',
    '''  const disposition = dispositionForExecutionOutcome(outcome.status, result);''',
)

# Coverage guard against adding a broadcaster that silently drops evidence.
write(
    "tests/unit/idempotency-broadcast-evidence-coverage.test.ts",
    '''import fs from "node:fs";\nimport path from "node:path";\nimport { describe, expect, it } from "vitest";\n\nconst ROOT = process.cwd();\nconst broadcasters = [\n  "plugins/web3/steps/transfer-funds-core.ts",\n  "plugins/web3/steps/transfer-token-core.ts",\n  "plugins/web3/steps/write-contract-core.ts",\n  "plugins/web3/steps/approve-token-core.ts",\n  "plugins/web3/steps/batch-write-contract-core.ts",\n  "plugins/robinhood/steps/trade-stock-token-core.ts",\n  "plugins/web3/steps/transfer-spl-token-core.ts",\n  "plugins/tempo/steps/tempo-tx-core.ts",\n];\n\ndescribe("idempotency broadcast evidence coverage", () => {\n  for (const file of broadcasters) {\n    it(`${file} preserves broadcast evidence`, () => {\n      const source = fs.readFileSync(path.join(ROOT, file), "utf8");\n      expect(source).toContain("broadcastAttempted");\n    });\n  }\n\n  it("the transfer route uses the shared evidence classifier for Solana", () => {\n    const source = fs.readFileSync(\n      path.join(ROOT, "app/api/execute/transfer/route.ts"),\n      "utf8"\n    );\n    expect(source).not.toContain("isSolanaTransfer && !result.transactionHash");\n  });\n});\n''',
)

print("changed files")
for path in changed:
    print(path)
