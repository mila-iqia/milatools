import sys
import setuptools
import versioneer

# with open("README.md", "r") as fh:
#     long_description = fh.read()
packages = setuptools.find_namespace_packages(include=["milavision*"])
print("PACKAGES FOUND:", packages)
print(sys.version_info)

setuptools.setup(
    name="milavision",
    version=versioneer.get_version(),
    cmdclass=versioneer.get_cmdclass(),
    author="Fabrice Normandin",
    author_email="normandf@mila.quebec",
    description="A drop-in replacement for torchvision.datasets when running on a cluster.",
    # long_description=long_description,
    # long_description_content_type="text/markdown",
    # url="https://github.com/lebrice/SimpleParsing",
    packages=packages,
    # package_data={"simple_parsing": ["py.typed"]},
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires=">=3.7",
    install_requires=["torchvision", "torch"],
)
