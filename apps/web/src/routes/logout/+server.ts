import { redirect } from '@sveltejs/kit';
import type { RequestHandler } from './$types';
import { clearSession } from '../../lib/auth/guard.server';

export const POST: RequestHandler = ({ cookies }) => {
  clearSession(cookies);
  throw redirect(303, '/');
};
