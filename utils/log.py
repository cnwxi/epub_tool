import os
import sys
import time


class logwriter:
    def __init__(self):
        self.path = os.path.join(
            os.path.dirname(os.path.abspath(sys.argv[0])), "log.txt"
        )
        # print(self.path)
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
