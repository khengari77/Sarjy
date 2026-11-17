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
  uniform float uBaseMix;
  uniform float uCoreHue;
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

  // Three octaves keep the colour islands soft; the fourth octave of fbm()
  // reads as speckle at these scales.
  float fbm3(vec2 p) {
    mat2 turn = mat2(0.8, 0.6, -0.6, 0.8);
    float value = 0.57 * vnoise(p);
    p = turn * p * 2.02 + 7.3;
    value += 0.29 * vnoise(p);
    p = turn * p * 2.02 + 7.3;
    return value + 0.14 * vnoise(p);
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
    float fine = vnoise(q * 9.0 + vec2(-t * 0.085, t * 0.07));
    float billow = low * 0.56 + mid * 0.34 + fine * 0.10;

    // Circular silhouette with only low-amplitude radial variation. The edge
    // fades over a wide band so the sphere ends in translucent mist, not a rim.
    float breathe = sin(t * 0.52) * (0.006 + uTurb * 0.006);
    float radius = 0.93 + (billow - 0.5) * (0.03 + uTurb * 0.02) + breathe + uPulse * 0.02;
    float edge = r - radius;
    // The cloud runs almost to the (CSS) ring; only a short fade at the rim.
    float body = 1.0 - smoothstep(-0.16, 0.05, edge);
    float core = 1.0 - smoothstep(0.0, 0.62, r);
    // Mid-radius band where the coloured islands live.
    float ring = smoothstep(0.10, 0.40, r) * (1.0 - smoothstep(0.78, 0.97, r));

    float density = body * (0.66 + 0.34 * billow);
    density = mix(density, 0.96, core * 0.7);

    // A diffuse base of the lightest tone, with several hues drifting through
    // the mid radii as separate soft islands (cyan, lavender, gold, mint, coral).
    vec3 col = mix(uColors[0], uColors[5], uBaseMix);
    float islandA = smoothstep(0.32, 0.62, fbm3(q * 1.3 + vec2(4.0, 1.0) + drift));
    float islandB = smoothstep(0.34, 0.64, fbm3(q * 1.35 + vec2(-6.0, 9.0) - drift));
    float islandC = smoothstep(0.36, 0.66, fbm3(q * 1.4 + vec2(13.0, -3.0) + drift * 1.4));
    float islandD = smoothstep(0.38, 0.68, fbm3(q * 1.45 + vec2(-2.0, -8.0) - drift * 1.2));
    col = mix(col, uColors[4], islandA * ring * 0.80);
    col = mix(col, uColors[2], islandB * ring * 0.78);
    col = mix(col, uColors[1], islandC * ring * 0.74);
    col = mix(col, uColors[3], islandD * ring * 0.70);
    col = mix(col, uColors[0], (1.0 - ring) * (1.0 - core) * 0.45);

    // Keep the hues from washing out on a pale page, without a flat core.
    float luma = dot(col, vec3(0.299, 0.587, 0.114));
    col = mix(vec3(luma), col, uSaturation) * uBodyShade;

    // Bright, diffuse, whitish centre.
    vec3 coreTint = mix(mix(uColors[0], uColors[5], uCoreHue), vec3(1.0), 0.78);
    col = mix(col, coreTint, core * core * uCoreMix);

    // Soft depth cue and diffuse top-left light.
    col *= 0.92 + 0.14 * low;
    float light = 1.0 - smoothstep(0.0, 1.2, length(p - vec2(-0.28, 0.32)));
    col += uColors[5] * light * (0.06 + uGlow * 0.05);

    // Phase tint stays legible without turning the cloud into confetti.
    col = mix(col, uPhase, 0.26 * smoothstep(0.2, 0.85, mid) * (0.4 + 0.6 * ring));

    if (uHasAccent > 0.5) {
      float accentField = smoothstep(0.55, 0.95, fbm(q * 2.4 + vec2(17.0, -4.0) + drift * 2.0));
      col = mix(col, uAccent, accentField * 0.55);
    }

    // A few thin wisp arcs: fixed crescents that drift slowly, never a
    // contour map of the billow field.
    float wisp = 0.0;
    vec2 wobble = vec2(sin(t * 0.09), cos(t * 0.07)) * 0.03;
    for (int i = 0; i < 4; i++) {
      float fi = float(i);
      vec2 d = p - (vec2(-0.30 + 0.26 * fi, 0.08 + 0.14 * sin(fi * 2.1)) + wobble);
      float heading = 1.2 + fi * 0.5;
      // Angular span from a dot product (no atan): 1 inside ~0.55 rad of the heading.
      float facing = dot(normalize(d), vec2(cos(heading), sin(heading)));
      float span = smoothstep(0.70, 0.94, facing);
      wisp = max(wisp, (1.0 - smoothstep(0.0, 0.011, abs(length(d) - (0.26 + 0.07 * fi)))) * span);
    }
    wisp *= body * (1.0 - core * 0.3);
    col = mix(col, mix(uColors[5], vec3(1.0), 0.6), wisp * 0.55);

    // Ten sparse dots in four loose clusters near the rim (x, y, size), not
    // an even scatter.
    float dots = 0.0;
    vec3 spots[10] = vec3[10](
      vec3(-0.34, 0.86, 0.012), vec3(-0.27, 0.89, 0.009), vec3(-0.40, 0.80, 0.008),
      vec3(-0.90, 0.30, 0.011), vec3(-0.93, 0.22, 0.008),
      vec3(0.93, 0.05, 0.010), vec3(0.95, -0.04, 0.008), vec3(0.91, -0.12, 0.007),
      vec3(0.12, -0.92, 0.010), vec3(0.20, -0.90, 0.008)
    );
    for (int i = 0; i < 10; i++) {
      dots = max(dots, 1.0 - smoothstep(spots[i].z * 0.5, spots[i].z, length(p - spots[i].xy - wobble * 0.2)));
    }
    col = mix(col, vec3(1.0), dots * 0.75);

    // The halo disc is CSS (.orb-glow); the canvas draws only the cloud.
    float alphaBody = body * (0.78 + 0.22 * min(1.0, density));
    // The canvas is a square; fade every layer out on a circle before the
    // border so nothing draws a straight rectangular edge.
    float edgeClip = 1.0 - smoothstep(0.965, 1.0, r);
    float alpha = max(alphaBody, dots * 0.8) * uOpacity * edgeClip;

    col = clamp(col, 0.0, 1.0);
    alpha = clamp(alpha, 0.0, 1.0);
    // Premultiplied: the canvas is created with premultipliedAlpha, so the
    // translucent fringe blends toward the page instead of toward black.
    outColor = vec4(col * alpha, alpha);
  }`;
