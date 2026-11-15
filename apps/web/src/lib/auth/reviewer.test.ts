import { describe, expect, it } from 'vitest';
import { isReviewerMode, isReviewerPublicPath, REVIEWER_COOKIE } from './reviewer';

describe('reviewer invitation mode', () => {
  it('is enabled only by the literal true', () => {
    expect(isReviewerMode('true')).toBe(true);
    expect(isReviewerMode('false')).toBe(false);
    expect(isReviewerMode('')).toBe(false);
    expect(isReviewerMode(undefined)).toBe(false);
    expect(isReviewerMode('1')).toBe(false);
  });

  it('keeps only the landing page and exchange open', () => {
    expect(isReviewerPublicPath('/review')).toBe(true);
    expect(isReviewerPublicPath('/review/')).toBe(true);
    expect(isReviewerPublicPath('/api/v1/reviewer/session')).toBe(true);
    expect(isReviewerPublicPath('/')).toBe(false);
    expect(isReviewerPublicPath('/api/v1/conversations')).toBe(false);
    expect(isReviewerPublicPath('/v1/demo-session')).toBe(false);
  });

  it('uses the same cookie the API sets, never an anonymous identity', () => {
    expect(REVIEWER_COOKIE).toBe('sarjy_demo');
  });
});
