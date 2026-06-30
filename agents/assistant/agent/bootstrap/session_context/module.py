import getpass


def process(ctx):
    ctx.bootstrap.add(
        "\n".join(
            [
                "Session environment:",
                f"- Workspace: `{ctx.bootstrap.workspace}`",
                f"- Current user: `{getpass.getuser()}`",
            ]
        )
    )
