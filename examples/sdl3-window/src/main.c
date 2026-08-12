#include <SDL3/SDL.h>
#include <SDL3/SDL_main.h>

static Uint64 timeout_read(int argc, char **argv)
{
    if (argc == 3 && SDL_strcmp(argv[1], "--timeout-ms") == 0) {
        return SDL_GetTicks() + (Uint64)SDL_atoi(argv[2]);
    }
    return 0;
}

int main(int argc, char **argv)
{
    SDL_Window *window = NULL;
    SDL_Renderer *renderer = NULL;
    SDL_Event event;
    Uint64 stop_at;
    bool running = true;

    if (!SDL_Init(SDL_INIT_VIDEO)) {
        SDL_Log("SDL_Init failed: %s", SDL_GetError());
        return 1;
    }
    if (!SDL_CreateWindowAndRenderer("Drift + SDL3", 800, 450, SDL_WINDOW_RESIZABLE, &window, &renderer)) {
        SDL_Log("SDL_CreateWindowAndRenderer failed: %s", SDL_GetError());
        SDL_Quit();
        return 1;
    }

    stop_at = timeout_read(argc, argv);
    while (running) {
        while (SDL_PollEvent(&event)) {
            if (event.type == SDL_EVENT_QUIT) {
                running = false;
            }
        }
        if (stop_at != 0 && SDL_GetTicks() >= stop_at) {
            running = false;
        }
        SDL_SetRenderDrawColor(renderer, 22, 30, 38, 255);
        SDL_RenderClear(renderer);
        SDL_SetRenderDrawColor(renderer, 39, 190, 158, 255);
        SDL_FRect panel = {80.0f, 80.0f, 640.0f, 290.0f};
        SDL_RenderFillRect(renderer, &panel);
        SDL_RenderPresent(renderer);
        SDL_Delay(1);
    }

    SDL_DestroyRenderer(renderer);
    SDL_DestroyWindow(window);
    SDL_Quit();
    return 0;
}
