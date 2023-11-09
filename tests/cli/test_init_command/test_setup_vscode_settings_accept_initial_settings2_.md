Calling `setup_vscode_settings()` with this initial content:

```json
{
    "foo": "bar"
}
```

and these user inputs: ('y',)
leads the following VsCode settings file:

```json
{
	"foo": "bar",
	"remote.SSH.connectTimeout": 60
}
```