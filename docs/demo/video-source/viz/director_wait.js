// rec director: let the page do its thing, end the recording when the page says it is done (window.__done)
await waitFor(() => window.__done === true, 900000, 'viz done');
