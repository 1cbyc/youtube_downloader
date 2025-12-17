using YoutubeDownloader.Services;

var builder = WebApplication.CreateBuilder(args);

// Add services
builder.Services.AddSingleton<DownloadQueueService>();
builder.Services.AddSingleton<YoutubeDownloadService>();
builder.Services.AddHostedService<BackgroundDownloadProcessor>();
builder.Services.AddControllers();
builder.Services.AddEndpointsApiExplorer();

var app = builder.Build();

// Configure the HTTP request pipeline
if (app.Environment.IsDevelopment())
{
    app.UseDeveloperExceptionPage();
}

app.UseStaticFiles();
app.UseDefaultFiles();

app.MapControllers();
app.MapFallbackToFile("index.html");

app.Run();
