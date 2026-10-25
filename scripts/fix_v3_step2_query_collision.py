from __future__ import annotations

from pathlib import Path

path = Path("docs/FRAMEWORK_BOOTSTRAP.md")
text = path.read_text(encoding="utf-8")
old = '''[[answers]]
intent = "onboarding"
question = "How do I onboard an arbitrary existing codebase into KAIROS?"
target = "s-handoff-into-a-project"
'''
new = '''[[answers]]
intent = "onboarding"
question = "Where does framework bootstrap hand off to universal project onboarding?"
target = "s-handoff-into-a-project"
'''
if text.count(old) != 1:
    raise SystemExit(f"expected one bootstrap onboarding query collision, found {text.count(old)}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
print("v3 step2 bootstrap query ownership disambiguated")
