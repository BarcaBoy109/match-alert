# Azure hosting

The bot runs on Azure Container Apps Consumption in **East Asia (Hong Kong)**,
in resource group `match-alert-rg`, app `match-alert`, environment `match-alert-env`.
Supabase continues to store all settings and alert state. GitHub Pages hosts the showcase.

Production readiness endpoint:
`https://match-alert.agreeableglacier-caf27abf.eastasia.azurecontainerapps.io/ready`

Render service `srv-daja9h9594qs73bdfvd0` is suspended as the rollback host.

The container has 0.25 vCPU and 0.5 GiB RAM. Both replica limits are 1, so the bot
stays running without incoming HTTP traffic. This consumes Azure student credit;
review Cost Management and the student credit balance periodically. No paid Azure
container registry or Log Analytics workspace is provisioned. Live logs remain
available with `az containerapp logs show -g match-alert-rg -n match-alert --follow`.

The image is built manually by `.github/workflows/container.yml` and published to
the public `ghcr.io/barcaboy109/match-alert` package with its full Git commit SHA.
Only application code goes into the image; runtime credentials are Azure secrets.

## Build and stage

Run from the repository root after signing into Azure CLI and GitHub CLI:

```powershell
gh workflow run container.yml --ref main
# Wait for the workflow to succeed, then use its full commit SHA below.
.venv\Scripts\python.exe azure/deploy.py --image ghcr.io/barcaboy109/match-alert:<full-commit-sha>
```

The script reads `DISCORD_TOKEN`, `DATABASE_URL`, and `POLL_MINUTES` from the local
`.env` or process environment. It sends credentials as ARM securestring parameters
through a temporary file, never as command-line values. It always stages with
`DISCORD_NOT_BEFORE` set to 1 January 2100, preventing Discord login until cutover.
`--location` can override `eastasia` when permitted by the subscription policy.

## Cutover and verification

1. Check `/live` on the Azure URL. It should return 200 while `/ready` returns 503
   during the staging pause. Verify the container can connect to Supabase.
2. Suspend the old Render service; keep it suspended as a rollback option.
3. Activate Azure:

```powershell
az containerapp update -g match-alert-rg -n match-alert --set-env-vars DISCORD_NOT_BEFORE=0 --output none
az containerapp show -g match-alert-rg -n match-alert --query properties.configuration.ingress.fqdn -o tsv
```

4. Check `/ready` returns 200 with `discord_ready: true`. Point external uptime
   monitoring at this URL. Infrastructure probes intentionally use `/live` so a
   Discord rate limit does not cause a restart loop.

Before future deployments, deactivate the running Azure revision(s) so two bot
processes cannot overlap. Single revision mode and a maximum of one replica do
not prevent temporary overlap during a rolling update. Stage the new image, then
activate it using the steps above. Do not blindly rerun deployment during a Discord
cooldown: preserve the existing retry deadline before allowing a new login.

To roll back hosting, deactivate all active Azure revisions, then resume Render.
Never resume Render while Azure is connected to Discord.

The ARM template is `azure/main.json`. Reapplying it pauses Discord login again.
Deployments change hosting only; they do not migrate or alter the Supabase schema.
