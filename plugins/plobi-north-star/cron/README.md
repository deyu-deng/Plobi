# North Star cron examples

After enabling the plugin, schedule with `hermes cron` (or the cronjob tool):

## Night tick (every 20 minutes, 00:00–07:00 local)

```
Prompt: Use skill plobi-night-autonomy. Call plobi_night_tick; if a task was
claimed, route and execute with summaries only; never auto-approve L2+.
```

## Morning report (07:05)

```
Prompt: Use skill plobi-morning-report and deliver to the user's preferred
gateway chat.
```

## Quota alert (every 6 hours)

```
Prompt: Use skill plobi-quota-alert.
```

24/7 process: `hermes gateway install` (OS service) so cron + messaging stay up.
