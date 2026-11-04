"""Build pinned native aerodynamic tools as an ordinary user on Linux.

Requires curl, git, patch, make, cmake, C/Fortran compilers and X11 headers/libs.
Run: python scripts/cem/build_native_aero.py --directory work/native-aero
Downloads stay in the supplied directory. No system install or service mutation.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile

SOURCES={
    'xfoil_6.99.dfsg+1.orig.tar.gz':'f5ece350981f35f7b5b4cef540c921ffad877b0942591efb88bad76cc08b597a',
    'xfoil_6.99.dfsg+1-3.debian.tar.xz':'b6688dc60a8437fbabb0da80fdceb623bcdd6c4f35ca8afbe130f817c6140837',
}
AVL_REVISION='b67837fa737afdf7a56655b044242619246b3ada'
FLAGS='-O0 -g -fbacktrace -fbounds-check -finit-real=inf -ffpe-trap=invalid,zero -fdefault-real-8 -fallow-argument-mismatch'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--fc',default='gfortran')
    parser.add_argument('--cmake',default='cmake')
    parser.add_argument('--fortran-link-flags',default='')
    parser.add_argument('--x11-include',default='/usr/include')
    parser.add_argument('--x11-library',default='-lX11')
    args=parser.parse_args();root=args.directory.resolve();root.mkdir(parents=True,exist_ok=True)
    if (root/'Xfoil').exists() or (root/'avl').exists():
        raise SystemExit('Choose an empty build directory; existing native sources are preserved.')
    patch=Path(__file__).resolve().parent/'native/xfoil-headless.patch'
    with (root/'build.log').open('w') as log:
        def run(argv,cwd=root):
            subprocess.run(argv,cwd=cwd,stdout=log,stderr=subprocess.STDOUT,check=True)
        for name,expected in SOURCES.items():
            target=root/name
            run(['curl','--fail','--location','https://deb.debian.org/debian/pool/main/x/xfoil/'+name,'-o',str(target)])
            if sha(target)!=expected: raise ValueError('Native source archive hash mismatch')
            with tarfile.open(target) as archive: archive.extractall(root,filter='data')
        for name in (root/'debian/patches/series').read_text().splitlines():
            run(['patch','-p1','-i',str(root/'debian/patches'/name)],root/'Xfoil')
        run(['patch','-p1','-i',str(patch)],root/'Xfoil')
        common=[f'FC={args.fc}',f'FFLAGS={FLAGS}',f'FFLOPT={FLAGS}',
                f'CPPFLAGS=-DUNDERSCORE -I{args.x11_include}',f'LDFLAGS={args.fortran_link_flags}']
        run(['make','-j2',*common],root/'Xfoil/plotlib')
        run(['make','-j2',*common,f'PLTLIB={args.x11_library}','xfoil'],root/'Xfoil/bin')
        run(['git','init','avl'])
        run(['git','remote','add','origin','https://github.com/RobotLocomotion/avl.git'],root/'avl')
        run(['git','fetch','--depth=1','origin',AVL_REVISION],root/'avl')
        run(['git','checkout','--detach',AVL_REVISION],root/'avl')
        options=[f'-DCMAKE_Fortran_COMPILER={args.fc}',
            '-DCMAKE_Fortran_FLAGS=-fallow-argument-mismatch '+args.fortran_link_flags,
            '-DDOUBLE_PRECISION=ON','-DCMAKE_POLICY_VERSION_MINIMUM=3.5',
            f'-DX11_X11_INCLUDE_PATH={args.x11_include}']
        if args.x11_library.startswith('/'):
            options.append(f'-DX11_X11_LIB={args.x11_library}')
        run([args.cmake,'-S',str(root/'avl'),'-B',str(root/'avl-build'),*options])
        run([args.cmake,'--build',str(root/'avl-build'),'-j2'])
    binaries=dict(xfoil=root/'Xfoil/bin/xfoil',avl=root/'avl-build/src/avl')
    manifest=dict(source_archives=SOURCES,avl_revision=AVL_REVISION,xfoil_flags=FLAGS,
        headless_patch_sha256=sha(patch),compiler=args.fc,compiler_version=subprocess.check_output([args.fc,'--version'],text=True),
        fortran_link_flags=args.fortran_link_flags,x11_include=args.x11_include,x11_library=args.x11_library,
        binaries={k:dict(path=str(p),sha256=sha(p)) for k,p in binaries.items()})
    (root/'build-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    for name,path in binaries.items(): print(f'MIRUM_{name.upper()}_BIN={path}')
    print('Build complete. Run native reference tests; executable success is not performance qualification.')


if __name__=='__main__': main()
