#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

int main() {
    while (1) {
        system("git add .");

        system("git commit -m 'building a youtube downloader that works'");

        system("git push");

        sleep(10);
    }

    return 0;
}
