/**
 * GLSL for the Living Cloud. A single full-screen triangle evaluates a 2D
 * cloud volume in the fragment shader: domain-warped billows, palette fields,
 * a cheap two-octave warp and a high-frequency detail band. No raymarcher and
 * no per-frame geometry upload.
 */
export const VERTEX = `#version 300 es
  void main() {
    vec2 corners[3] = vec2[3](vec2(-1.0, -1.0), vec2(3.0, -1.0), vec2(-1.0, 3.0));
    gl_Position = vec4(corners[gl_VertexID], 0.0, 1.0);
  }`;

export const FRAGMENT = `#version 300 es
  precision highp float;

  uniform vec2 uResolution;
  uniform float uTime;
  uniform float uPulse;
  uniform float uTurb;
  uniform float uFlow;
  uniform float uGlow;
  uniform float uOpacity;
  uniform float uBodyShade;
  uniform float uSaturation;
  uniform float uCoreMix;
  uniform vec3 uColors[6];
  uniform vec3 uPhase;
  uniform vec3 uAccent;
  uniform float uHasAccent;
  out vec4 outColor;

  float hash(vec2 p) {
    p = fract(p * vec2(127.1, 311.7));
    p += dot(p, p + 34.23);
    return fract(p.x * p.y);
  }

  float vnoise(vec2 p) {
    vec2 i = floor(p);
    vec2 f = fract(p);
    vec2 u = f * f * (3.0 - 2.0 * f);
    return mix(
      mix(hash(i), hash(i + vec2(1.0, 0.0)), u.x),
      mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), u.x),
      u.y
    );
  }

  float fbm(vec2 p) {
    float value = 0.0;
    float amplitude = 0.55;
    mat2 turn = mat2(0.8, 0.6, -0.6, 0.8);
    for (int i = 0; i < 4; i++) {
      value += amplitude * vnoise(p);
      p = turn * p * 2.02 + 7.3;
      amplitude *= 0.5;
    }
    return value;
  }

  // Two octaves are enough for the large-scale domain warp; keeping the warp
  // cheap leaves the fragment budget for the body and detail bands.
  float fbm2(vec2 p) {
    mat2 turn = mat2(0.8, 0.6, -0.6, 0.8);
    return 0.67 * vnoise(p) + 0.33 * vnoise(turn * p * 2.02 + 7.3);
  }

  void main() {
    vec2 uv = gl_FragCoord.xy / uResolution;
    vec2 p = (uv - 0.5) * 2.0;
    p.x *= uResolution.x / max(1.0, uResolution.y);

    float r = length(p);
    float t = uTime;
    float speed = 0.35 + uFlow * 0.85;
    vec2 drift = vec2(t * 0.032 * speed, -t * 0.026 * speed);

    // Low-frequency domain warp gives billows rather than a spinning ball.
    vec2 warp = vec2(
      fbm2(p * 1.5 + drift + 3.1),
      fbm2(p * 1.5 - drift + 11.7)
    ) - 0.5;
    vec2 q = p + warp * (0.16 + uTurb * 0.22);

    float low = fbm(q * 2.0 + drift);
    float mid = fbm(q * 4.1 - drift * 1.35 + low * 0.9);
    float fine = vnoise(q * 12.0 + vec2(-t * 0.085, t * 0.07));
    float billow = low * 0.52 + mid * 0.33 + fine * 0.15;

    // Circular silhouette with only low-amplitude radial variation. The
    // boundary fades over ~0.26 of the field, so there is no wire rim.
    float breathe = sin(t * 0.52) * (0.006 + uTurb * 0.006);
    float radius = 0.865 + (billow - 0.5) * (0.045 + uTurb * 0.03) + breathe + uPulse * 0.02;
    float edge = r - radius;
    float body = 1.0 - smoothstep(-0.10, 0.18, edge);
    float core = 1.0 - smoothstep(0.0, 0.84, r);

    // The centre keeps at least ~70% of the body's perceived density.
    float density = body * (0.62 + 0.38 * billow);
    density = mix(density, 0.94, core * 0.72);

    vec3 col = mix(uColors[0], uColors[1], smoothstep(0.18, 0.82, low));
    col = mix(col, uColors[2], smoothstep(0.42, 0.92, mid) * 0.62);
    col = mix(col, uColors[3], (1.0 - smoothstep(0.16, 0.58, abs(low - mid))) * 0.30);
    col = mix(col, uColors[4], smoothstep(0.58, 0.95, vnoise(q * 2.6 + 8.0)) * 0.24);

    // Sky Prism pastels are close to the page background, so the body is
    // shaded down and slightly saturated while the luminous core stays pale.
    // This is shading of the contract palette, not a second invented palette.
    float luma = dot(col, vec3(0.299, 0.587, 0.114));
    col = mix(vec3(luma), col, uSaturation) * uBodyShade;

    // The centre stays cloudy and filled: a pale version of the dominant body
    // colour, not pure white, so density survives against a light page.
    vec3 coreTint = mix(uColors[0], uColors[5], 0.35);
    col = mix(col, coreTint, core * uCoreMix);

    // Depth cue: darker folds sit behind brighter billows.
    col *= 0.84 + 0.26 * low;

    // Diffuse top-left illumination, strongest through the middle.
    float light = 1.0 - smoothstep(0.0, 1.35, length(p - vec2(-0.32, 0.36)));
    col += uColors[5] * light * (0.08 + uGlow * 0.06);

    // Keep the phase colour legible without turning the cloud into confetti.
    col = mix(col, uPhase, 0.32 * smoothstep(0.15, 0.82, mid));

    if (uHasAccent > 0.5) {
      float accentField = smoothstep(0.55, 0.95, fbm(q * 2.4 + vec2(17.0, -4.0) + drift * 2.0));
      col = mix(col, uAccent, accentField * 0.62);
    }

    // Sparse highlights, not a starfield.
    float sparkle = smoothstep(0.958, 0.997, vnoise(q * 30.0 + vec2(t * 0.4, -t * 0.3)));
    col += uColors[5] * sparkle * 0.35 * body;

    float alphaBody = body * (0.70 + 0.24 * min(1.0, density));
    float haloBand = 1.0 - smoothstep(0.90, 1.20, r);
    float halo = haloBand * (1.0 - body) * (0.06 + 0.09 * (0.5 + 0.5 * low) + uGlow * 0.03);
    // The canvas is a square; fade every layer out on a circle before the
    // border so the halo never draws a straight rectangular edge.
    float edgeClip = 1.0 - smoothstep(0.90, 0.985, r);
    float alpha = max(alphaBody, halo) * uOpacity * edgeClip;

    col = clamp(col, 0.0, 1.0);
    outColor = vec4(col, clamp(alpha, 0.0, 1.0));
  }`;
