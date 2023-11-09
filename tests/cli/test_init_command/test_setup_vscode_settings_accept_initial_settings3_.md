Calling `setup_vscode_settings()` with this initial content:

```json
{
    "remote.SSH.connectTimeout": 123
}
```

and these user inputs: ('y',)
leads the following VsCode settings file:

```json
{
    "remote.SSH.connectTimeout": 60
}
```