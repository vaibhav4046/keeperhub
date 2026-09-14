from pathlib import Path


def read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    Path(path).write_text(text, encoding="utf-8", newline="\n")


def add_import(path: str) -> None:
    text = read(path)
    statement = 'import { broadcastTransactionHash } from "@/lib/web3/onchain-revert";'
    if statement in text:
        return
    marker = 'import "server-only";\n'
    if marker not in text:
        raise SystemExit(f"{path}: missing server-only")
    write(path, text.replace(marker, marker + statement + "\n", 1))


def patch_action(path: str, input_t: str, result_t: str) -> None:
    add_import(path)
    text = read(path)
    old_type = '| { success: false; error: string };'
    if text.count(old_type) != 1:
        raise SystemExit(f"{path}: failure result arm count={text.count(old_type)}")
    text = text.replace(
        old_type,
        '''| {\n      success: false;\n      error: string;\n      transactionHash?: string;\n      chainId?: number;\n      broadcastAttempted?: boolean;\n    };''',
        1,
    )
    old_decl = f'''async function stepHandler(\n  input: {input_t}\n): Promise<{result_t}> {{'''
    new_decl = f'''async function stepHandlerImpl(\n  input: {input_t}\n): Promise<{result_t}> {{'''
    if text.count(old_decl) != 1:
        raise SystemExit(f"{path}: handler declaration count={text.count(old_decl)}")
    text = text.replace(old_decl, new_decl, 1)
    try_marker = '''  try {\n    const rpcManager = await getRpcProvider({'''
    if text.count(try_marker) != 1:
        raise SystemExit(f"{path}: main try marker count={text.count(try_marker)}")
    text = text.replace(
        try_marker,
        '''  let broadcastHash: string | undefined;\n\n  try {\n    const rpcManager = await getRpcProvider({''',
        1,
    )
    link_marker = '''\n    const transactionLink = await buildTempoTxLink(chainId, hash);'''
    if text.count(link_marker) != 1:
        raise SystemExit(f"{path}: link marker count={text.count(link_marker)}")
    text = text.replace(
        link_marker,
        '''\n    broadcastHash = hash;\n    const transactionLink = await buildTempoTxLink(chainId, hash);''',
        1,
    )
    catch_marker = '''    return { success: false, error: getErrorMessage(error) };'''
    pos = text.rfind(catch_marker)
    if pos < 0:
        raise SystemExit(f"{path}: terminal catch missing")
    catch_new = '''    const transactionHash = broadcastHash ?? broadcastTransactionHash(error);\n    return {\n      success: false,\n      error: getErrorMessage(error),\n      broadcastAttempted: transactionHash ? true : false,\n      ...(transactionHash ? { transactionHash, chainId } : {}),\n    };'''
    text = text[:pos] + catch_new + text[pos + len(catch_marker):]
    export_pos = text.find("\nexport async function ")
    if export_pos < 0:
        raise SystemExit(f"{path}: exported step marker missing")
    wrapper = f'''\nasync function stepHandler(input: {input_t}): Promise<{result_t}> {{\n  const result = await stepHandlerImpl(input);\n  if (result.success || result.broadcastAttempted !== undefined) {{\n    return result;\n  }}\n  return {{ ...result, broadcastAttempted: false }};\n}}\n'''
    text = text[:export_pos] + wrapper + text[export_pos:]
    write(path, text)


for args in [
    ("plugins/tempo/steps/transfer-with-memo.ts", "TransferWithMemoInput", "TransferWithMemoResult"),
    ("plugins/tempo/steps/dex-swap.ts", "DexSwapInput", "DexSwapResult"),
    ("plugins/tempo/steps/batch-payout.ts", "BatchPayoutInput", "BatchPayoutResult"),
]:
    patch_action(*args)

print("tempo action evidence patch applied")
