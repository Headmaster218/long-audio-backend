"""Portable parent-death supervisor for FFmpeg/ffprobe and their descendants."""
import subprocess
import sys
import time
import psutil


def main():
    parent_id=int(sys.argv[1]); parent_created=float(sys.argv[2]); args=sys.argv[3:]
    child=subprocess.Popen(args,stdin=subprocess.DEVNULL)
    try:
        while child.poll() is None:
            try:
                parent=psutil.Process(parent_id)
                alive=abs(parent.create_time()-parent_created)<.01 and parent.status()!=psutil.STATUS_ZOMBIE
            except psutil.Error: alive=False
            if not alive:
                try: descendants=psutil.Process(child.pid).children(recursive=True)
                except psutil.Error: descendants=[]
                for process in descendants:
                    try:process.kill()
                    except psutil.Error:pass
                child.kill();child.wait(timeout=3)
                return 70
            time.sleep(.2)
        return child.returncode
    finally:
        if child.poll() is None: child.kill()

if __name__=='__main__':sys.exit(main())
