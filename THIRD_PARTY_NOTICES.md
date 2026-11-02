# Third-party notices

The browser client vendors the following libraries under `static/vendor/` so
the app can run without a CDN. Each file retains its upstream license header.

| Library | Version | License | Project |
| --- | --- | --- | --- |
| marked | 18.1.1 | MIT | <https://github.com/markedjs/marked> |
| DOMPurify | 3.4.17 | MPL-2.0 OR Apache-2.0 | <https://github.com/cure53/DOMPurify> |
| highlight.js | 11.12.0 | BSD-3-Clause | <https://github.com/highlightjs/highlight.js> |

No third-party network requests are made by the client. The model server
(Ollama by default) is contacted only by the server process.
