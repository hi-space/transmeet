#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { TransmeetStack } from '../lib/transmeet-stack';

const app = new cdk.App();

new TransmeetStack(app, 'TransmeetStack', {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: 'us-east-1',
  },
  description: 'TransMeet - Real-time meeting translation infrastructure',
});
