param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ScriptArgs
)
python -m dedupe.cli @ScriptArgs
