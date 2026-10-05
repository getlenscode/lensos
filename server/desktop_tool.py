"""The only desktop capability exposed to the Pi voice agent."""

import json
import sys

from voice_commands import (CommandError, desktop_lock, execute_command,
                            layout_summary, parse_command)


def main():
    if len(sys.argv) != 2:
        print(json.dumps({"error": "Expected one desktop command."}))
        return 2
    try:
        with desktop_lock():
            command = parse_command(sys.argv[1])
            result = {"action": command["action"], **execute_command(command)}
        # Give the agent fresh desktop state so it can verify the action
        # before replying, without spending an extra tool call on it.
        state = layout_summary()
        if state:
            result["windows"] = state
        print(json.dumps(result))
        return 0
    except (CommandError, OSError) as error:
        print(json.dumps({"error": str(error)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
