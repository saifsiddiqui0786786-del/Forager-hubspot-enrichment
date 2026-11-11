# Notes: extra Forager data worth discussing

## Already captured (as custom properties, easy to remove)

**Contacts**
- **LinkedIn headline** - the one-line tagline under the name.
- **LinkedIn About** - the full "About" section text.
- **Skills** - the skills listed on the profile.

**Companies**
- Industry, employee range, keywords, legal name, operating status, logo URL.

## Available from Forager but not captured yet

**People**
- **Full work history** - every past role with title, employer, dates and description. Useful for "previously at a customer" or "new in role" triggers.
- **Education, certifications, languages, courses** - useful for personalisation.
- **Honors, patents, publications, projects, volunteering** - niche, but strong hooks for senior or technical people.
- **Photo and gender.**
- **Personal emails** - currently used only as a fallback when no work email is found.
- **Email validation status** - Forager marks each email `valid`, `risky`, `invalid` or `unknown`. Worth storing so risky emails can be held back from sending.
- **Reverse lookups** - find a person from just an email or a phone number.

**Companies**
- **Funding events** - round type, amount and date (sourced from Crunchbase).
- **Job postings** - open roles with title, location and remote flag. A strong hiring-intent signal.
- **Web technologies and traffic** - the tech stack detected on the website, plus domain rank.
- **Company events** - for example reports released or promotions on site.

## Data quality observations

- **Several records per domain.** Searching `openai.com` returns 37 organisations, mostly look-alike or fan pages. The service resolves the right one by scoring name and domain.
- **Fuzzy title search.** A search for "Director" also returned "Team Manager". Results are re-checked in code.
- **Unverified employees.** Some profiles that list OpenAI as their employer are clearly not real staff. Requiring a verified work email is the best filter.
- **Messy locations.** Some people carry locations like "united states, bangladesh". Only Forager's typed city / state / country parts are trusted.
- **Phone coverage is lower than email coverage**, and phones cost more, so email is checked first.

## Questions for discussion

1. Should the LinkedIn About section be stored on the contact, or only used for personalisation at send time?
2. Should a personal email ever be written to the main Email field, or kept in a separate property?
3. Is a `risky` email acceptable for the CRM, or `valid` only?
4. Which phone tier is preferred: `highest_quality` (fewer numbers) or `most_coverage`?
5. Should funding and hiring signals be synced as company properties or as timeline events?
