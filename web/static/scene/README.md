# Scene assets

Use this structure for static scene layers:

- `assets/sky/`
- `assets/sky_elements/`
- `assets/background/`
- `assets/foreground/`

Main config: `scene.json`

Example item in any layer:

```json
{
  "id": "sun-01",
  "kind": "image",
  "src": "/static/scene/assets/sky_elements/sun.png",
  "alt": "Sun",
  "style": {
    "top": "6%",
    "right": "12%",
    "width": "180px",
    "opacity": "0.9",
    "zIndex": "2"
  }
}
```

Supported style keys:

- `left`, `right`, `top`, `bottom`
- `width`, `height`
- `minWidth`, `maxWidth`, `minHeight`, `maxHeight`
- `transform`, `opacity`, `zIndex`, `filter`, `mixBlendMode`
