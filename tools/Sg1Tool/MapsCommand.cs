using System.Globalization;
using UELib;
using UELib.Core;

namespace Sg1Tool;

/// <summary>
/// Per-map actor inventory.
///
/// A `.unr` is a package whose exports are the level's actors, so counting exports by class gives
/// the actor census of each map without parsing the level structure. That census is the evidence
/// base for cut-content work: "map X contains N actors of class Y" is a citable fact, and a class
/// that is referenced by no shipped map is a candidate for content that was never placed.
/// </summary>
internal static class MapsCommand
{
    public static int Run(string dumpRoot, string outDir)
    {
        var gameDir = Dump.GameDir(dumpRoot);
        var mapsDir = Path.Combine(gameDir, "Maps");
        if (!Directory.Exists(mapsDir))
            throw new DirectoryNotFoundException($"no Maps/ directory under {gameDir}");

        var maps = Directory.GetFiles(mapsDir, "*.unr", SearchOption.TopDirectoryOnly)
            .OrderBy(p => p, StringComparer.Ordinal).ToList();

        var perMap = new List<object?[]>();
        var perClass = new List<object?[]>();
        var failures = new List<object?[]>();

        foreach (var path in maps)
        {
            var name = Path.GetFileNameWithoutExtension(path);
            UnrealPackage package;
            try
            {
                package = UnrealLoader.LoadPackage(path, FileAccess.Read);
                package.NTLPackage = Dump.TableForPackage(dumpRoot, path);
                package.InitializePackage(UnrealPackage.InitFlags.All);
            }
            catch (Exception ex)
            {
                failures.Add(new object?[] { name, ex.GetType().Name, ex.Message });
                Console.Error.WriteLine($"map {name}: FAILED {ex.Message}");
                continue;
            }

            var exports = package.Objects.Where(Dump.IsExport).ToList();
            var byClass = exports
                .GroupBy(o => o.Class?.Name.ToString() ?? "(none)")
                .ToDictionary(g => g.Key, g => g.Count());
            var actors = byClass.Where(kv => !IsStructural(kv.Key)).Sum(kv => kv.Value);
            perMap.Add(new object?[]
            {
                name,
                new FileInfo(path).Length,
                exports.Count,
                actors,
                byClass.Count,
                string.Join("|", byClass.OrderByDescending(kv => kv.Value).Take(5)
                    .Select(kv => $"{kv.Key}={kv.Value}"))
            });
            foreach (var kv in byClass.OrderBy(kv => kv.Key, StringComparer.Ordinal))
                perClass.Add(new object?[] { name, kv.Key, kv.Value });
            Console.Error.WriteLine($"map {name}: {exports.Count} exports, {actors} actors, {byClass.Count} classes");
        }

        Csv.Write(Path.Combine(outDir, "maps.csv"),
            new[] { "map", "bytes", "exports", "actor_exports", "distinct_classes", "top_classes" },
            perMap.OrderBy(r => Csv.SortKey(r[0]), StringComparer.Ordinal));
        Csv.Write(Path.Combine(outDir, "map_actors.csv"),
            new[] { "map", "class", "count" },
            perClass.OrderBy(r => Csv.SortKey(r[0]), StringComparer.Ordinal)
                .ThenBy(r => Csv.SortKey(r[1]), StringComparer.Ordinal));
        Csv.Write(Path.Combine(outDir, "map_parse.csv"), new[] { "map", "exception", "message" }, failures);
        Console.Error.WriteLine($"maps: {perMap.Count}/{maps.Count} parsed -> {outDir}");
        return 0;
    }

    /// <summary>Exports that are level structure rather than placed actors.</summary>
    private static bool IsStructural(string className) => className is
        "Level" or "Model" or "Polys" or "LevelSummary" or "None" or "(none)" or
        "Sound" or "Texture" or "StaticMesh" or "MeshAnimation" or "Material" or "ScriptStruct" or "Class";
}
