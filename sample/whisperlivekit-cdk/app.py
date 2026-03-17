#!/usr/bin/env python3
import aws_cdk as cdk
from stacks.whisperlivekit_stack import WhisperLiveKitStack

app = cdk.App()

WhisperLiveKitStack(
    app,
    "WhisperLiveKitStack",
    env=cdk.Environment(
        account=app.node.try_get_context("account"),
        region=app.node.try_get_context("region") or "ap-northeast-2",
    ),
)

app.synth()
