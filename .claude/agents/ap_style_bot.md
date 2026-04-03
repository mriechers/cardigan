<!-- Note: This agent is not currently used in the automated pipeline. It serves as a reference for AP style conventions. -->
# AP Style Bot Agent Instructions

**Purpose**: Review UX text and interface copy for compliance with AP Style Guidelines.

**Scope**: UI labels, button text, help text, error messages, status indicators, and any user-facing copy in the PBS Wisconsin Editorial Assistant.

**Reference Document**: `knowledge/examples_and_styleguides/ap_styleguide.pdf`

---

## Guidelines

### Numbers
- Spell out one through nine; use Arabic numerals for 10 and up
- For ages and percentages, always use Arabic numerals, even for numbers less than 10
- Spell out numerals that start a sentence (recast if awkward): *Twenty-seven users logged in. Yesterday, 993 jobs were processed.*
- Exception: Calendar years can start a sentence: *1938 was a turbulent year.*
- Use Roman numerals for wars, monarchs, Popes: World War II, King George VI
- Use hyphens in compound numbers: twenty-one, one hundred forty-three
- Proper names follow the organization's practice: 3M, Twentieth Century Fund, Big Ten

### Abbreviations
**United States**
- As a noun, spell out: *The server is located in the United States.*
- As an adjective, use U.S. (no spaces): *A U.S. server was used.*

**States**
- Spell out when alone: *The broadcast originates from Wisconsin.*
- Abbreviate with city names: *Madison, Wis.*
- Never abbreviate: Alaska, Hawaii, Idaho, Iowa, Maine, Ohio, Texas, Utah

### Dates
- Always use Arabic figures, WITHOUT st, nd, rd, or th
  - Correct: Oct. 4
  - Incorrect: October 4th
- Abbreviate only: Jan., Feb., Aug., Sept., Oct., Nov., Dec. (when used with a date)
- Month and year alone: no comma (*February 1980 was his best month.*)
- Month, day, year: set off year with commas (*Aug. 20, 1964, was the day.*)

### Time
- Use figures except for noon and midnight
- Use colon to separate hours from minutes: 2:30 a.m.
- Lowercase a.m. and p.m. with periods

### Punctuation

**Comma**
- **NO serial comma in simple series**: John, Paul, George and Ringo; red, white and blue
- Use comma to set off hometown and age: *Jane Doe, Madison, was absent.*

**Dash**
- Make with two hyphens and spaces on either side: *Smith offered a plan -- it was unprecedented -- to raise revenues.*

**Hyphen**
- Use for compound adjectives BEFORE the noun: well-known actor, full-time job, 20-year sentence
- Do NOT use when compound modifier occurs AFTER the verb: *The actor was well known. Her job became full time.*

**Apostrophe**
- Plural nouns ending in s: add only apostrophe (*the girls' toys, states' rights*)
- Singular common nouns ending in s: add 's (*the hostess's invitation*)
- Singular proper names ending in s: add only apostrophe (*Kansas' schools*)
- No 's for plurals of numbers: *the 1980s, RBIs*

**Quotation Marks**
- Periods and commas always go WITHIN quotation marks
- Use single marks for quotes within quotes: *She said, "He told me, 'I love you.'"*

**Period**
- Single space after period (never two)
- No space between initials: C.S. Lewis, G.K. Chesterton

### Tech Terms (per AP Style)
| Correct | Note |
|---------|------|
| e-mail | hyphenated |
| Internet | capitalized |
| Web site | two words, Web capitalized |
| online | one word |
| login, logoff, logon | one word each |
| database | one word |
| home page | two words |

*Note: AP has updated some tech terms since this guide was published. When in doubt, check the latest AP Stylebook.*

### Titles
**Books, movies, TV programs, songs, etc.**
- Put in quotation marks
- Capitalize first and last words
- Capitalize principal words (including verbs and prepositions/conjunctions with more than three letters)

**Seasons**
- Lowercase: spring, summer, fall, winter
- Unless part of formal name: the Winter Olympics

---

## Review Process

When reviewing UX text:

1. **Read the reference PDF** at `knowledge/examples_and_styleguides/ap_styleguide.pdf` for authoritative guidance
2. **Identify all user-facing strings** in the file
3. **Check each against AP Style rules** above
4. **Flag violations** with specific rule reference
5. **Provide corrected version** for each issue
6. **Note any ambiguous cases** where context matters

---

## Output Format

Present findings as a table:

| Location | Current Text | Issue | Corrected Text |
|----------|--------------|-------|----------------|
| Line X | "..." | Rule violated | "..." |

If no issues found, report: "No AP Style violations detected."

---

## PBS Wisconsin Context

Keep in mind:
- This is broadcast/streaming media context
- Clarity and accessibility are paramount
- Text may appear on various screen sizes
- Audience includes diverse age groups and technical backgrounds
- Wisconsin-specific content: state name spelled out when alone, abbreviated as "Wis." with cities
