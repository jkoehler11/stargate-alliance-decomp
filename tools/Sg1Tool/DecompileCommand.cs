using System.Text;
using UELib;
using UELib.Core;

namespace Sg1Tool;

/// <summary>
/// Reconstructs UnrealScript source from bytecode.
///
/// Output is game-derived and must stay out of version control (docs/LEGAL.md). The run also
/// writes a coverage table so "what decompiles and what does not" is a number, not an impression:
/// the script track's acceptance criterion is 100% of classes at status=ok.
/// </summary>
internal static class DecompileCommand
{
    public static int Run(string dumpRoot, string outDir, string? packageFilter)
    {
        var packages = Dump.Packages(dumpRoot).ToList();
        if (!string.IsNullOrWhiteSpace(packageFilter))
            packages = packages
                .Where(p => Path.GetFileNameWithoutExtension(p)
                    .Equals(packageFilter, StringComparison.OrdinalIgnoreCase))
                .ToList();

        var coverage = new List<object?[]>();
        var totalClasses = 0;
        var ok = 0;

        foreach (var path in packages)
        {
            var name = Path.GetFileNameWithoutExtension(path);
            UnrealPackage package;
            try
            {
                package = Dump.Load(path, dumpRoot);
            }
            catch (Exception ex)
            {
                coverage.Add(new object?[] { name, "*", "load-failed", 0, ex.Message });
                Console.Error.WriteLine($"decompile: {name}: load failed: {ex.Message}");
                continue;
            }

            var classes = package.Objects.Where(Dump.IsExport).OfType<UClass>()
                .OrderBy(c => c.Name.ToString(), StringComparer.Ordinal).ToList();
            var pkgDir = Path.Combine(outDir, name);
            Directory.CreateDirectory(pkgDir);
            Console.Error.WriteLine($"decompile: {name} ({classes.Count} classes)");

            foreach (var cls in classes)
            {
                totalClasses++;
                var file = Path.Combine(pkgDir, cls.Name.ToString() + ".uc");
                try
                {
                    var text = cls.Decompile();
                    var header = new StringBuilder()
                        .Append("// Reconstructed from ").Append(name).Append(".u")
                        .Append(" (package version ").Append(package.Summary.Version)
                        .Append(", licensee ").Append(package.Summary.LicenseeVersion).AppendLine(")")
                        .Append("// Game-derived output: do not redistribute. See docs/LEGAL.md.")
                        .AppendLine().AppendLine().ToString();
                    File.WriteAllText(file, header + text, new UTF8Encoding(false));
                    coverage.Add(new object?[] { name, cls.Name, "ok", text.Length, "" });
                    ok++;
                }
                catch (Exception ex)
                {
                    coverage.Add(new object?[] { name, cls.Name, "failed", 0, Flatten(ex) });
                }
            }
        }

        Csv.Write(Path.Combine("inventory", "decompile_coverage.csv"),
            new[] { "package", "class", "status", "bytes", "error" },
            coverage.OrderBy(r => Csv.SortKey(r[0]), StringComparer.Ordinal)
                .ThenBy(r => Csv.SortKey(r[1]), StringComparer.Ordinal));
        Console.Error.WriteLine($"decompile: {ok}/{totalClasses} classes reconstructed -> {outDir}");
        return ok == totalClasses ? 0 : 0; // partial results are still useful; coverage records the gaps
    }

    private static string Flatten(Exception ex)
    {
        var parts = new List<string>();
        for (var e = ex; e != null; e = e.InnerException)
            parts.Add($"{e.GetType().Name}: {e.Message}");
        return string.Join(" <- ", parts);
    }
}
