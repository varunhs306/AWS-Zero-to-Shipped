# Proof: coding agent connected to AWS

Before I Leave was built with **Claude Code** connected to AWS through the **AWS MCP Server** (Agent Toolkit for AWS, via `mcp-proxy-for-aws`), signed in with `aws login` as the project IAM user `dev_user`.

| # | Evidence | What it shows |
|---|---|---|
| 1 | [01-mcp-server-connected.png](01-mcp-server-connected.png) | Claude Code's MCP server list: `aws-mcp` connected with 8 tools |
| 2 | [02-mcp-server-configuration.png](02-mcp-server-configuration.png) | The server configuration: `uvx mcp-proxy-for-aws@latest https://aws-mcp.us-east-1.api.aws/mcp` — Connected |
| 3 | [03-first-aws-call-through-mcp.png](03-first-aws-call-through-mcp.png) | The first AWS call through the MCP server (`sts:GetCallerIdentity`), returning the project IAM user |
| 4 | [04-cloudtrail-event-first-mcp-call.json](04-cloudtrail-event-first-mcp-call.json) | The CloudTrail record of that call: `eventSource: aws-mcp.amazonaws.com`, tool `aws___run_script`, downstream `sts:GetCallerIdentity`, user agent `mcp-proxy-for-aws/1.7.0 claude-code/2.1.281` (AWS hides the access key and IP address) |

## CloudTrail totals (September 24 to October 2, 2026)

- **120+ calls** through the AWS MCP Server (`eventSource: aws-mcp.amazonaws.com`), all by `dev_user`, and every one with a user agent naming `mcp-proxy-for-aws` and `claude-code`.
- **14 deployments** of the app's CloudFormation stack (2 creates, 12 updates), all made through `aws-mcp.amazonaws.com`.

## Check it yourself

With access to the account, these commands list the oldest MCP calls and the first stack deployments:

```bash
aws cloudtrail lookup-events --region us-east-1 \
  --lookup-attributes AttributeKey=EventSource,AttributeValue=aws-mcp.amazonaws.com --start-time 2026-09-20 \
  --query "sort_by(Events,&EventTime)[:10].{Time:EventTime,Event:EventName,User:Username,Source:EventSource}" --output table

aws cloudtrail lookup-events --region us-east-1 \
  --lookup-attributes AttributeKey=EventName,AttributeValue=UpdateStack --start-time 2026-09-20 \
  --query "sort_by(Events,&EventTime)[:5].{Time:EventTime,Event:EventName,User:Username}" --output table
```
