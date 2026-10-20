"""Trusted POSIX exec wrapper: enforce limits before the requested program starts."""
import os
import sys


def main():
    import resource
    memory_bytes=int(sys.argv[1])
    if memory_bytes<=0 or len(sys.argv)<3:raise ValueError('Invalid process memory envelope')
    resource.setrlimit(resource.RLIMIT_AS,(memory_bytes,memory_bytes))
    command=sys.argv[2:]
    os.execvpe(command[0],command,os.environ)


if __name__=='__main__':main()
