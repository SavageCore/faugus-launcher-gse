project(
  'faugus-launcher',
  version: '1.22.8',
  meson_version: '>=1.2.0',
  license: 'MIT',
  license_files: 'LICENSE',
)

py = import('python').find_installation()
fs = import('fs')
gnome = import('gnome')

subdir('assets')
subdir('languages')
subdir('data')
subdir('faugus')

install_data(
  'faugus-launcher',
  install_mode: 'rwxr-xr-x',
  install_dir: get_option('bindir'),
)

install_data(
  'faugus_run.py',
  rename: 'faugus-run',
  install_mode: 'rwxr-xr-x',
  install_dir: get_option('bindir'),
)

install_data(
  'LICENSE',
  install_dir: get_option('datadir') / 'licenses' / meson.project_name(),
)

gse_fork_files = run_command('find', 'gse_fork/linux', '-type', 'f', check: false).stdout().strip().split('\n')
foreach f : gse_fork_files
  if f != '' and not f.endswith('.gitkeep')
    install_data(
      f,
      install_dir: get_option('datadir') / 'faugus-launcher' / 'gse_fork',
      rename: f.split('/')[-1],
    )
  endif
endforeach

gnome.post_install(
  gtk_update_icon_cache: true,
)
