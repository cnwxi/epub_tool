import os
import sys
import time


class logwriter:
    def __init__(self):
        env_path = os.environ.get("EPUB_TOOL_LOG_PATH", "").strip()
        if env_path:
            self.path = os.path.abspath(env_path)
        else:
            self.path = os.path.join(
                os.path.dirname(os.path.abspath(sys.argv[0])), "log.txt"
            )
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            current_time = time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime(time.time())
            )
            f.write(f"time: {current_time}\n")

    def write(self, text):
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(f"{text}\n")


if __name__ == "__main__":
    log = logwriter()
    log.write("hello world")
