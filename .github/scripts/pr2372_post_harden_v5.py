from pathlib import Path


def read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    Path(path).write_text(text, encoding="utf-8", newline="\n")


def exact(path: str, old: str, new: str, count: int = 1) -> None:
    text = read(path)
    found = text.count(old)
    if found != count:
        raise SystemExit(f"{path}: expected {count}, found {found}: {old[:100]!r}")
    write(path, text.replace(old, new, count))


def add_import(path: str, statement: str) -> None:
    text = read(path)
    if statement in text:
        return
    marker = 'import "server-only";\n'
    if marker not in text:
        raise SystemExit(f"{path}: no server-only marker")
    write(path, text.replace(marker, marker + statement + "\n", 1))


def wrap_export(path: str, fn: str, input_type: str, result_type: str) -> None:
    text = read(path)
    exported = f"export async function {fn}("
    internal = f"async function {fn}Impl("
    if internal in text:
        return
    if text.count(exported) != 1:
        raise SystemExit(f"{path}: expected one export for {fn}")
    text = text.replace(exported, internal, 1)
    wrapper = f'''\n\n/** Explicitly marks every hashless early return as pre-broadcast evidence. */\nexport async function {fn}(\n  input: {input_type}\n): Promise<{result_type}> {{\n  const result = await {fn}Impl(input);\n  if (result.success || result.broadcastAttempted !== undefined) {{\n    return result;\n  }}\n  return {{ ...result, broadcastAttempted: false }};\n}}\n'''
    write(path, text.rstrip() + wrapper)


NETWORK_IMPORT = 'import { isDefinitelyPreBroadcastNetworkError } from "@/lib/web3/submit-signed";'

# write-contract differs from the older sandbox transform because current HEAD
# already caches broadcastTransactionHash(error) in a local. Capture the receipt
# hash immediately after the send too, before explorer/trace decoration can fail.
path = "plugins/web3/steps/write-contract-core.ts"
add_import(path, NETWORK_IMPORT)
exact(
    path,
    '''    try {\n      let receipt: Awaited<ReturnType<typeof adapter.executeContractCall>>;''',
    '''    let receivedTransactionHash: string | undefined;\n    try {\n      let receipt: Awaited<ReturnType<typeof adapter.executeContractCall>>;''',
)
exact(
    path,
    '''      const gasUsedUnits = receipt.gasUsed.toString();''',
    '''      receivedTransactionHash = receipt.hash;\n      const gasUsedUnits = receipt.gasUsed.toString();''',
)
exact(
    path,
    '''      const broadcastHash = broadcastTransactionHash(error);''',
    '''      const broadcastHash =\n        broadcastTransactionHash(error) ?? receivedTransactionHash;''',
)
exact(
    path,
    '''        ...(errorClass ? { errorClass } : {}),\n        ...(rejection.kind !== "unknown" ? { rejection } : {}),''',
    '''        ...(errorClass ? { errorClass } : {}),\n        broadcastAttempted: broadcastHash\n          ? true\n          : rejection.kind !== "unknown" ||\n              isDefinitelyPreBroadcastNetworkError(error)\n            ? false\n            : true,\n        ...(rejection.kind !== "unknown" ? { rejection } : {}),''',
)
wrap_export(path, "writeContractCore", "WriteContractCoreInput", "WriteContractResult")

# The older transform handled these cores' evidence fields/catches. Strengthen
# them by retaining a successfully returned receipt hash across any subsequent
# explorer/trace/formatting exception.
for path, start_marker in [
    (
        "plugins/web3/steps/transfer-token-core.ts",
        '''    // Create contract instance for the actual write (needs signer)\n    const contract = new ethers.Contract(tokenAddress, ERC20_ABI, signer);\n\n    try {''',
    ),
    (
        "plugins/web3/steps/approve-token-core.ts",
        '''    // Keep contract instance for error formatting in catch block\n    const contract = new ethers.Contract(tokenAddress, ERC20_ABI, signer);\n\n    try {''',
    ),
]:
    exact(
        path,
        start_marker,
        start_marker[:-6] + '''    let receivedTransactionHash: string | undefined;\n    try {''',
    )
    exact(
        path,
        '''      const gasUsedUnits = receipt.gasUsed.toString();''',
        '''      receivedTransactionHash = receipt.hash;\n      const gasUsedUnits = receipt.gasUsed.toString();''',
    )
    exact(
        path,
        '''      const rejection = classifyRevert(error, contract.interface);''',
        '''      const rejection = classifyRevert(error, contract.interface);\n      const broadcastHash =\n        broadcastTransactionHash(error) ?? receivedTransactionHash;''',
    )
    exact(path, '''          broadcastTransactionHash(error)\n            ? true''', '''          broadcastHash ? true''')
    exact(
        path,
        '''        ...(broadcastTransactionHash(error)\n          ? { transactionHash: broadcastTransactionHash(error), chainId }\n          : {}),''',
        '''        ...(broadcastHash ? { transactionHash: broadcastHash, chainId } : {}),''',
    )

# Native EVM transfer has a matching post-receipt explorer step. The Solana
# send catch is deliberately left hashless+attempted (fail closed) by v4.
path = "plugins/web3/steps/transfer-funds-core.ts"
exact(
    path,
    '''    try {\n      let receipt: Awaited<ReturnType<typeof adapter.sendTransaction>>;''',
    '''    let receivedTransactionHash: string | undefined;\n    try {\n      let receipt: Awaited<ReturnType<typeof adapter.sendTransaction>>;''',
)
exact(
    path,
    '''      const gasUsedUnits = receipt.gasUsed.toString();''',
    '''      receivedTransactionHash = receipt.hash;\n      const gasUsedUnits = receipt.gasUsed.toString();''',
    1,
)
exact(
    path,
    '''      const rejection = classifyRevert(error);''',
    '''      const rejection = classifyRevert(error);\n      const broadcastHash =\n        broadcastTransactionHash(error) ?? receivedTransactionHash;''',
)
exact(path, '''          broadcastTransactionHash(error)\n            ? true''', '''          broadcastHash ? true''')
exact(
    path,
    '''        ...(broadcastTransactionHash(error)\n          ? { transactionHash: broadcastTransactionHash(error), chainId }\n          : {}),''',
    '''        ...(broadcastHash ? { transactionHash: broadcastHash, chainId } : {}),''',
)

# Batch write also decorates the receipt after the adapter returns.
path = "plugins/web3/steps/batch-write-contract-core.ts"
exact(
    path,
    '''    try {\n      const receipt = await adapter.executeContractCall(''',
    '''    let receivedTransactionHash: string | undefined;\n    try {\n      const receipt = await adapter.executeContractCall(''',
)
exact(
    path,
    '''      const gasUsedUnits = receipt.gasUsed.toString();''',
    '''      receivedTransactionHash = receipt.hash;\n      const gasUsedUnits = receipt.gasUsed.toString();''',
)
exact(
    path,
    '''      const broadcastHash = broadcastTransactionHash(error);''',
    '''      const broadcastHash =\n        broadcastTransactionHash(error) ?? receivedTransactionHash;''',
)

# Route-level regression fixtures must distinguish proven pre-broadcast refusal
# from absent evidence now that the classifier fails closed by default.
path = "tests/unit/execute-protocol-idempotency-disposition.test.ts"
text = read(path)
needle = '''      success: false,\n      error: "LK: not yet due",\n'''
count = text.count(needle)
if count != 4:
    raise SystemExit(f"{path}: expected four LK pre-broadcast fixtures, found {count}")
text = text.replace(
    needle,
    '''      success: false,\n      error: "LK: not yet due",\n      broadcastAttempted: false,\n''',
)
write(path, text)

# Pin the core contract directly: omitted evidence holds; explicit false is the
# only hashless failed shape that releases.
path = "tests/unit/idempotency-disposition-evidence.test.ts"
text = read(path)
if 'fails closed when a failed execution has no broadcast evidence' not in text:
    raise SystemExit(f"{path}: v4 classifier tests were not applied")

print("post-hardening patch applied")
