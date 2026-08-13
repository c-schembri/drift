from pathlib import Path


def generate_message(arguments):
    output = Path(arguments[0])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("hello from drift\n", encoding="utf-8")
