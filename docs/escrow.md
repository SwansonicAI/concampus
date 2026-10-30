# Escrow

What HfM gets if ConCampus can no longer run Campus, and how. Planned, not set up. A Hochschule that depends on one
small provider for its student records will ask for this in the tender.

## What is deposited

- **Code**: the repository with its history; the deployment (`Dockerfile`, Compose, Caddy, `deploy.sh`, the CI
  workflow); a guide to build and run Campus, tested on a clean machine. The repository is public now; once it is
  private, the deposit is the copy HfM can count on.
- **Access**: every account that runs Campus for HfM, and the way into it:
  - the server host (Hetzner): the project and the root SSH key;
  - the database host (Neon now);
  - the code host (GitHub): the repository and the secrets of its Actions;
  - domain and DNS (Porkbun): concampus.de and HfM's subdomain;
  - the server's `.env`: `SECRET_KEY`, `DATABASE_URL`, `EMAIL_URL`;
  - backups and their keys;
  - the connection to HfM's dakota, if we look after it (`docs/smv.md`).
- **Not HfM's data**: it stays where it is hosted, and the access above reaches it. A depository holding personal data
  would be one more processor.

## Keeping it current

- The code with every release, deposited by CI; access whenever a password or key changes.
- Each year the escrow agent checks the deposit: Campus builds and starts from it alone.
- Passwords in a deposit go stale and are one more place to steal them from. Better: an emergency account in each
  service, used only after release, its use logged; or HfM as co-owner of its hosting account from the start.

## Release

Conditions set in the contract, for instance: ConCampus insolvent or dissolved; the person running it dead or lastingly
unable to; maintenance ended; a critical fault not fixed within the agreed time. The agent needs proof of the condition,
so someone must be named who can give it. On release HfM, or a firm it names, gets the deposit.

## Rights

A deposit is useless without the right to use it. On release HfM may use Campus, change it and have others maintain it,
for its own purposes. The licence is granted now with release as its condition, so that it holds in insolvency
(§ 103 InsO); to be drafted with a lawyer.

## To do

1. An escrow agent and an agreement between ConCampus, HfM and the agent.
2. The build guide, tested on a clean machine.
3. CI depositing each release.
4. Emergency access to each account.
