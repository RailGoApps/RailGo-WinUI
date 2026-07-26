import re

# ======================
# 车次号（G / D / C）
# ======================

def extract_train_no(text: str) -> str:
    text = text.upper()
    match = re.search(r"\b([GDC]\d{1,4})\b", text)
    return match.group(1) if match else ""


def is_train_no(text: str) -> bool:
    return bool(extract_train_no(text))

def classify_train_no(train_no: str) -> str:
    """
    Returns:
    - 'hsr'  : G/D/C (rail.re 支持)
    - 'normal': Z/T/K/Y (rail.re 不支持)
    - 'invalid'
    """
    if not train_no:
        return "invalid"

    train_no = train_no.upper()

    if train_no[0] in ("G", "D", "C"):
        return "hsr"

    if train_no[0] in ("Z", "T", "K", "Y"):
        return "normal"

    return "invalid"
# ======================
# 动车组号（CR / CRH）
# ======================

def extract_emu_no(text: str) -> str:
    text = text.upper()

    match = re.search(
        r"\b(CRH?\d{3,4}[A-Z]{0,4}-?\d{3,4})\b",
        text
    )
    if not match:
        return ""

    emu = match.group(1)
    # 🚨 rail.re 不允许 "-"
    emu = emu.replace("-", "")
    return emu


def is_emu_no(text: str) -> bool:
    return bool(extract_emu_no(text))