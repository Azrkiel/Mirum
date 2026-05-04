| id | domain | difficulty | status | lvm | features | time_ms | regression_for | notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A_csv_bolt_threads_timeout | A | medium | pass | 5.0 | 5/5 | 12300 | A | User-feedback CSV (4/30/2026): 'Could not reach backend ... read timeout=180'. Rated 1. Tagged A; once Worktree A makes this reach Domain B, the threads shou... |
| A_csv_bus_driver_figurine | A | medium | pass | 3.0 | 2/5 | 29330 | A | User-feedback CSV (4/30/2026): 'A soulful bus driver figurine ... result just wouldn't load. Connection error.' Rated 2. Worktree A fix. |
| A_csv_cube_no_load | A | easy | pass | 5.0 | 1/1 | 8860 | A | User-feedback CSV (4/30/2026): pipeline never returned for this prompt. Worktree A (job queue + worker scaling) should make this complete < 30s reliably. |
| A_csv_dog_cupcake | A | medium | pass | 4.7 | 2/4 | 19670 | A | User-feedback CSV (4/30/2026): user-prompt 'a dog ... cupcake ... scary pitbull wearing a bow' ran 15 minutes and failed. Captured here as an A regression be... |
| A_csv_mouse_shell_timeout | A | medium | pass | 5.0 | 4/4 | 31720 | A | User-feedback CSV (4/30/2026): 'Could not reach backend ... read timeout=180'. Rated 1. A separate organic-shell topology case is captured by C_csv_spoon_fun... |
| A_csv_simple_circle | A | easy | pass | 5.0 | 3/3 | 8910 | A | User-feedback CSV (4/30/2026): multiple users reported 'a circle' / 'a blue circle' / '3D Circle' prompts buffering and never returning, OR returning 'Could ... |
| A_csv_simple_square | A | easy | pass | 5.0 | 2/2 | 9250 | A | User-feedback CSV (4/30/2026): 'Build a square' / 'Draw a square' buffered then died with read-timeout. Worktree A fix. |
| A_csv_smooth_sphere_3in | A | easy | pass | 5.0 | 3/3 | 9340 | A | User-feedback CSV (4/30/2026): 'It refused to build it, just would not generate.' Rated 2. Captured as A since the failure was a refusal/timeout; once the pi... |
| D_csv_f14_jet | D | hard | fail |  |  | 161360 | A,C | User-feedback CSV (4/30/2026): planning ran for 5 minutes then 'Could not connect to backend (Connection error)'. Worktree A (move planning off the synchrono... |
