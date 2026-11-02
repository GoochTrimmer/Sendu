-- Add Yahoo! Auctions JP as a marketplace (decided 2026-11-02).
insert into marketplaces (code, name) values ('yahoo_auctions', 'Yahoo! Auctions JP')
on conflict (code) do nothing;
