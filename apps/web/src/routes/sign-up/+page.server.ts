import type { Actions, PageServerLoad } from './$types';
import { accountEntry } from '../../lib/auth/accountEntry.server';

const entry = accountEntry('sign-up');

export const load: PageServerLoad = (event) => entry.load(event);
export const actions: Actions = { default: (event) => entry.submit(event) };
